#!/usr/bin/env bash
# create-toy-greenfield.sh — Create the fib-fast toy project.
#
# Exercises all three kits (TDD, Research, Math) end-to-end,
# including cross-kit interop.
#
# Usage:
#   master-kit/examples/create-toy-greenfield.sh [target-dir]
#
# Default target: /tmp/fib-fast

set -euo pipefail

TARGET="${1:-/tmp/fib-fast}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MASTER_KIT_SRC="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -d "$TARGET" ]]; then
  echo "[toy] ERROR: $TARGET already exists. Remove it first or choose a different path." >&2
  exit 1
fi

echo "[toy] creating fib-fast project at $TARGET"
mkdir -p "$TARGET"
cd "$TARGET"

# ── Step 1: Greenfield install ────────────────────────────────────────────────

echo "[toy] copying master-kit into project"
cp -R "$MASTER_KIT_SRC" "$TARGET/master-kit"
# Remove nested .git so fib-fast has a clean repo (not an embedded submodule).
rm -rf "$TARGET/master-kit/.git"

echo "[toy] running greenfield install (--skip-smoke)"
"$TARGET/master-kit/install.sh" --skip-smoke

# ── Step 2: Seed domain content ──────────────────────────────────────────────

echo "[toy] writing seed files"

# --- TDD: PRD.md ---
cat > PRD.md <<'EOF'
# fib-fast

## Product Requirements Document

---

## 1. Goal

A Python library computing Fibonacci numbers via multiple algorithms,
with a CLI for benchmarking and comparison.

**Success metric:** `fib(n)` returns the correct Fibonacci number for
0 ≤ n ≤ 10 000 across all algorithm variants, with ≥95% test coverage.

---

## 2. Constraints

| Constraint | Decision |
|------------|----------|
| Language   | Python 3.12 |
| Framework  | None (pure Python, no deps) |
| Testing    | pytest |
| Build      | pip (pyproject.toml) |

---

## 3. Non-Goals (v1)

- Arbitrary-precision performance tuning (GMP bindings)
- Async / concurrent computation
- Publishing to PyPI

---

## 4. Architecture

```
src/
  __init__.py         # public API: fib(n, method=...)
  naive.py            # recursive O(2^n)
  memoized.py         # top-down DP O(n)
  matrix.py           # matrix exponentiation O(log n)
  binet.py            # closed-form (float, diverges for large n)
  cli.py              # argparse CLI: fib <n> [--method] [--bench]
tests/
  test_naive.py
  test_memoized.py
  test_matrix.py
  test_binet.py
  test_cli.py
```

---

## 5. Build Order

| Step | Description | Status |
|------|-------------|--------|
| 1    | Core `fib(n)` with matrix exponentiation | Not started |
| 2    | Naive + memoized variants | Not started |
| 3    | Binet variant with float-precision warning | Not started |
| 4    | CLI with `--method` and `--bench` flags | Not started |
EOF

# --- TDD: LAST_TOUCH.md ---
cat > LAST_TOUCH.md <<'EOF'
# Last Touch — Cold-Start Briefing

## What to do next

Start TDD cycle for Step 1: core `fib(n)` with matrix exponentiation.
Spec is at `docs/fib-library.md`. Run:

```bash
./tdd.sh red docs/fib-library.md
```

## Key files for current task

| File | Role |
|------|------|
| `docs/fib-library.md` | TDD spec for the core fib(n) function |
| `PRD.md` | Full product requirements |
| `src/__init__.py` | Public API (to be created by GREEN phase) |
| `src/matrix.py` | Matrix exponentiation impl (to be created) |

## Don't waste time on

- **Build verification** — no build artifacts yet, fresh project.
- **Dependency checks** — pure Python, no external deps.
- **Codebase exploration** — read this file and PRD.md.

## Architecture overview

See PRD.md §4. Four algorithm variants behind a unified `fib(n)` API.

## Test coverage

- 0 tests — project is brand new.
EOF

# --- TDD: docs/fib-library.md ---
cat > docs/fib-library.md <<'EOF'
# Spec: Core fib(n) with Matrix Exponentiation

## What to build

A function `fib(n: int) -> int` that computes the n-th Fibonacci number
using matrix exponentiation in O(log n) time.

## Interface

```python
from fib_fast import fib

assert fib(0) == 0
assert fib(1) == 1
assert fib(10) == 55
assert fib(50) == 12586269025
```

## Algorithm

Uses the identity: `[[1,1],[1,0]]^n = [[F(n+1),F(n)],[F(n),F(n-1)]]`.
Compute the matrix power via repeated squaring.

## Edge cases

- `fib(0) == 0`, `fib(1) == 1`
- `fib(-1)` raises `ValueError`
- `fib(n)` for large n (n=1000) returns correct arbitrary-precision int

## Acceptance criteria

1. All tests pass with `pytest tests/`
2. `fib(n)` matches known values for n in {0,1,2,10,50,100,1000}
3. Negative input raises `ValueError`
EOF

# --- Research: QUESTIONS.md ---
cat > QUESTIONS.md <<'EOF'
# Research Questions

The research agenda for the fib-fast project.

---

## 1. Goal

Characterize the performance and correctness tradeoffs between Fibonacci
algorithm variants to inform default algorithm selection.

**Success looks like:** A clear recommendation for which algorithm to use
as the default, backed by empirical crossover-point data and formal
error bounds for the Binet approximation.

---

## 2. Constraints

| Constraint | Decision |
|------------|----------|
| Framework  | Pure Python + timeit |
| Compute    | Single-core laptop |
| Timeline   | 1 day |
| Baselines  | Naive recursive, memoized DP |

---

## 3. Non-Goals (This Phase)

- GPU-accelerated Fibonacci (not meaningful for this problem)
- Parallelized matrix exponentiation
- Benchmarking against C/Rust implementations

---

## 4. Open Questions

| Priority | Question | Status | Parent | Blocker | Decision Gate | Experiment(s) |
|----------|----------|--------|--------|---------|---------------|---------------|
| P0 | Does Binet's formula diverge from true Fibonacci for n > 70? | Not started | — | — | If divergent, need integer-only fallback | — |
| P1 | At what n does matrix exponentiation beat memoized recursion? | Not started | — | — | Determines default algorithm selection | — |
| P2 | Can we achieve O(1) amortized with LRU cache + matrix hybrid? | Not started | P1 | P1 answered | Only pursue if crossover exists | — |

---

## 5. Answered Questions

| Question | Answer Type | Answer | Evidence |
|----------|-------------|--------|----------|

---

## 6. Working Hypotheses

- Binet diverges around n=70-80 due to IEEE 754 double precision limits.
- Matrix exponentiation is faster than memoized for n > ~20.
- A hybrid (cache small n, matrix for large n) could give O(1) amortized for common inputs.
EOF

# --- Research: RESEARCH_LOG.md ---
cat > RESEARCH_LOG.md <<'EOF'
# Research Log

Cumulative findings from all experiments. Each entry is a concise summary — full details are in the linked analysis documents.

Read this file FIRST when starting any new research task. It is the institutional memory of this project.

---

<!-- New entries go at the top. Format:

## [exp-NNN-name] — [CONFIRMED/REFUTED/INCONCLUSIVE]
**Date:** YYYY-MM-DD
**Hypothesis:** [one line]
**Key result:** [one line with the critical number]
**Lesson:** [one line — what we learned]
**Next:** [one line — what to do about it]
**Details:** results/exp-NNN/analysis.md

-->

_No experiments yet. Run your first cycle with `./experiment.sh survey "your question"`._
EOF

# --- Research: DOMAIN_PRIORS.md ---
cat > DOMAIN_PRIORS.md <<'EOF'
# Domain Priors

Knowledge injected by the research lead. The SURVEY and FRAME agents
MUST read this file and incorporate these priors into experiment design.

## Problem Structure

- Fibonacci is a linear recurrence: F(n) = F(n-1) + F(n-2).
- Naive recursion has exponential time complexity O(2^n).
- Memoized/DP approaches are O(n) time, O(n) space.
- Matrix exponentiation is O(log n) time, O(1) space.
- Binet's formula is O(1) time but uses floating-point, introducing error for large n.

## Known Architecture-Problem Mappings

- For n < ~20: any method is fast enough, memoized is simplest.
- For 20 < n < ~70: matrix and Binet are both fast and accurate.
- For n > ~70: Binet diverges from true F(n) due to IEEE 754 limits.
- For n > ~1000: matrix exponentiation is the only practical exact method.

## Anti-Patterns to Avoid

- Don't benchmark with n < 10 — all methods are sub-microsecond.
- Don't use `time.time()` for microbenchmarks — use `timeit` with sufficient repeats.
- Don't compare wall-clock across different machines without normalization.

## Domain-Specific Guidance

- Python's arbitrary-precision integers make matrix method exact for all n.
- Binet requires `math.sqrt(5)` which is a float64 — precision limit is inherent.
- The golden ratio phi = (1+sqrt(5))/2 ≈ 1.618033988749895.
EOF

# --- Math: CONSTRUCTIONS.md ---
cat > CONSTRUCTIONS.md <<'EOF'
# Constructions Queue

Program mode reads this file to auto-advance through mathematical constructions.

## Priority Queue

| Priority | Construction | Spec File | Status | Depends On | Notes |
|----------|-------------|-----------|--------|------------|-------|
| P0 | Matrix Fibonacci Identity | `specs/fib-matrix-identity.md` | Not started | — | Required before Research can use matrix method as ground-truth oracle |
| P1 | Binet Error Bound | `specs/fib-binet-bound.md` | Not started | P0 | Formal bound on \|Binet(n) - F(n)\| for IEEE 754 doubles |

### Status Values
- **Not started** — spec not yet written
- **Specified** — spec complete, ready for construction
- **Constructed** — informal math done, ready for formalization
- **Formalized** — .lean files written (all sorry)
- **Proved** — all sorrys eliminated
- **Audited** — passed audit, logged
- **Revision** — needs revision (see REVISION.md)
- **Blocked** — blocked on dependency

---

## Completed

| Construction | Spec File | Date Completed | Theorems |
|-------------|-----------|----------------|----------|

---

## Dependencies
<!-- P1 (Binet Error Bound) depends on P0 (Matrix Identity) — uses matrix identity as ground truth -->
EOF

# --- Math: CONSTRUCTION_LOG.md ---
cat > CONSTRUCTION_LOG.md <<'EOF'
# Construction Log

Cumulative record of all construction audit results.

---

## Log Entries

<!-- Each audit appends an entry below this line -->
<!-- Template for each entry:

### [Construction Name] — [Date]
- **Spec**: `specs/[name].md`
- **Lean files**: `[path/to/file.lean]`
- **lake build**: PASS / FAIL
- **sorry count**: 0
- **axiom count**: 0
- **native_decide count**: 0

#### Coverage
| Spec Property | Lean4 Theorem | Status |
|--------------|---------------|--------|
| [property]   | [theorem]     | PROVED |

#### Verdict: PASS / FAIL / REVISION_NEEDED
#### Notes
[Observations]

---
-->
EOF

# --- Math: DOMAIN_CONTEXT.md ---
cat > DOMAIN_CONTEXT.md <<'EOF'
# Domain Context

Domain knowledge, Mathlib mappings, and notation conventions for this project.

## Domain Description

Number theory and linear algebra for Fibonacci sequence identities.
We formalize the matrix exponentiation identity and derive error bounds
for the Binet (closed-form) approximation.

## Mathlib Type Mappings

| Domain Concept | Mathlib Type | Module |
|---------------|-------------|--------|
| Fibonacci number | `Nat.fib` | `Mathlib.Data.Nat.Fib.Basic` |
| 2x2 Matrix | `Matrix (Fin 2) (Fin 2) ℤ` | `Mathlib.Data.Matrix.Basic` |
| Matrix power | `M ^ n` | `Mathlib.Data.Matrix.Basic` |
| Golden ratio | `(1 + Real.sqrt 5) / 2` | `Mathlib.Analysis.SpecialFunctions.Pow` |
| Absolute value | `|x|` / `abs x` | `Mathlib.Algebra.Order.AbsoluteValue` |

## Notation Table

| Symbol | Lean4 | Meaning |
|--------|-------|---------|
| F(n) | `Nat.fib n` | n-th Fibonacci number |
| Q | `!![1,1;1,0]` | Fibonacci Q-matrix |
| Q^n | `Q ^ n` | n-th power of Q-matrix |
| φ | `(1 + Real.sqrt 5) / 2` | Golden ratio |
| ψ | `(1 - Real.sqrt 5) / 2` | Conjugate golden ratio |

## Key Mathlib Lemmas

| Lemma | Module | Used For |
|-------|--------|----------|
| `Nat.fib_add_two` | `Data.Nat.Fib.Basic` | F(n+2) = F(n+1) + F(n) |
| `Nat.fib_zero` | `Data.Nat.Fib.Basic` | F(0) = 0 |
| `Nat.fib_one` | `Data.Nat.Fib.Basic` | F(1) = 1 |
| `Matrix.mul_pow` | `Data.Matrix.Basic` | (A*B)^n for commuting matrices |

## Project-Specific Conventions

- Follow Mathlib naming conventions (`snake_case` for definitions, descriptive theorem names)
- Use `namespace FibFast` to organize related definitions
- Prefer `structure` over `class` for concrete mathematical objects
- Use Mathlib typeclasses for abstract algebraic structures
- Name key theorems: `fib_matrix_identity`, `binet_error_bound`

## Known Limitations

- Mathlib's `Nat.fib` returns `Nat`, so integer matrix identity needs coercion
- Real-number analysis in Lean4 requires careful handling of `Real.sqrt`

## DOES NOT APPLY
<!-- Record failed approaches here during PROVE phase. -->
EOF

# --- Math: specs/fib-matrix-identity.md ---
cat > specs/fib-matrix-identity.md <<'EOF'
# Spec: Matrix Fibonacci Identity

## Statement

For all n ≥ 0:

```
[[1,1],[1,0]]^n = [[F(n+1), F(n)], [F(n), F(n-1)]]
```

where F(n) is the n-th Fibonacci number with F(0)=0, F(1)=1.

## Formal Statement (Lean4 sketch)

```lean
theorem fib_matrix_identity (n : ℕ) :
  (!![1,1;1,0] : Matrix (Fin 2) (Fin 2) ℤ) ^ n =
    !![↑(Nat.fib (n+1)), ↑(Nat.fib n);
       ↑(Nat.fib n), ↑(Nat.fib (n-1+1) - if n = 0 then 1 else 0)] := by
  sorry
```

Note: the (0,0) and (1,1) entries need care at n=0 boundary.

## Proof Strategy

1. Induction on n
2. Base case n=0: Q^0 = I = [[1,0],[0,1]] and F(1)=1, F(0)=0, F(-1)=1 by convention
3. Inductive step: Q^(n+1) = Q^n * Q, then expand using F(n+2) = F(n+1) + F(n)

## Properties to Verify

| Property | Description |
|----------|-------------|
| `base_case` | Identity holds for n = 0 |
| `inductive_step` | If identity holds for n, it holds for n+1 |
| `main_theorem` | The full identity for all n ≥ 0 |

## Dependencies

- `Nat.fib` from Mathlib
- `Matrix.mul_apply` for matrix multiplication
- `Matrix.pow_succ` for inductive step
EOF

# --- Project infrastructure ---
mkdir -p src tests
touch src/__init__.py
touch tests/__init__.py

# --- Lean4 project (for math kit formalization) ---
if command -v lake &>/dev/null; then
  echo "[toy] initializing Lean4 project for math kit"
  lake init fib_fast_lean math 2>&1 | head -5
  echo "[toy]   Lean4 project initialized"
else
  echo "[toy] WARN: lake not found — math kit FORMALIZE/PROVE phases will fail"
  echo "[toy]   install elan (https://github.com/leanprover/elan) to enable"
fi

echo "[toy] seed files written"

# ── Step 3: Overwrite CLAUDE.md ──────────────────────────────────────────────

cat > CLAUDE.md <<'EOF'
# fib-fast — Project Instructions

## What This Is

A Python library computing Fibonacci numbers via multiple algorithms.
Exercises all three master-kit kits: TDD (build it), Research (benchmark it),
Math (prove it).

## Available Kits

| Kit | Script | Phases |
|-----|--------|--------|
| **TDD** | `./tdd.sh` | red, green, refactor, ship, full |
| **Research** | `./experiment.sh` | survey, frame, run, read, log, cycle, full, program, status |
| **Math** | `./math.sh` | survey, specify, construct, formalize, prove, audit, log, full, program, status |

## Orchestrator

```bash
source .master-kit.env
master-kit/tools/kit --json <kit> <phase> [args...]
```

Run artifacts land in `master-kit/runs/<run_id>/`.

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
- `src/` — Python source code
- `tests/` — pytest test files

## Cross-Kit Interop

| From | To | Trigger |
|------|----|---------|
| Research | Math | "Binet diverges at n>70 — need formal error bound proof" |
| Math | Research | "Matrix identity proven — use as ground truth oracle" |
| Research | TDD | "Matrix method fastest for n>20 — add as default impl" |
| TDD | Math | "Need correctness guarantee before shipping public API" |

## Don't

- Don't `cd` into `master-kit/` and run kit scripts from there.
- Don't `cat` full log files — use `master-kit/tools/query-log`.
- Don't explore the codebase to "understand" it — read state files first.
- **Don't independently verify kit sub-agent work.** Each phase spawns a dedicated sub-agent that does its own verification. Trust the exit code and capsule. Do NOT re-run tests, re-read logs, re-check build output, or otherwise duplicate work the sub-agent already did. Exit 0 + capsule = done. Exit 1 = read the capsule for the failure, don't grep the log.
- Don't read phase log files after a successful phase. The capsule is the summary. Logs are for debugging failures only.

## Breadcrumb Maintenance (MANDATORY)

After every session that changes the codebase, update:

1. **`LAST_TOUCH.md`** — Current state and what to do next (TDD).
2. **`RESEARCH_LOG.md`** — Append experiment results (Research).
3. **`CONSTRUCTION_LOG.md`** — Progress notes (Math).
4. **This file's "Current State" section** — Keep it current.

## Current State (updated $(date +%Y-%m-%d))

- **Build:** No build yet — project is brand new.
- **Tests:** 0 tests.
- **Experiments completed:** 0
- **Constructions completed:** 0
- **Next task (TDD):** Step 1 — core `fib(n)` with matrix exponentiation. Spec: `docs/fib-library.md`.
- **Next task (Research):** P0 — Does Binet's formula diverge for n > 70?
- **Next task (Math):** P0 — Prove matrix Fibonacci identity. Spec: `specs/fib-matrix-identity.md`.
EOF

echo "[toy] CLAUDE.md updated"

# ── Step 4: Init git + first commit ──────────────────────────────────────────

echo "[toy] initializing git repo"
git init -q
git add -A
git commit -q -m "init: fib-fast toy project"

echo "[toy] git repo initialized with initial commit"

# ── Step 5: Verify ───────────────────────────────────────────────────────────

echo "[toy] running verification"

source .master-kit.env

echo "[toy] verifying research status..."
research_out="$(master-kit/tools/kit --json research status 2>&1)" || {
  echo "[toy] ERROR: research status failed" >&2
  echo "$research_out" >&2
  exit 1
}
echo "[toy]   research status: OK"

echo "[toy] verifying math status..."
math_out="$(master-kit/tools/kit --json math status 2>&1)" || {
  echo "[toy] ERROR: math status failed" >&2
  echo "$math_out" >&2
  exit 1
}
echo "[toy]   math status: OK"

echo ""
echo "[toy] fib-fast project created at: $TARGET"
echo "[toy] quick start:"
echo "  cd $TARGET"
echo "  source .master-kit.env"
echo "  master-kit/tools/kit --json tdd red docs/fib-library.md"
echo "  master-kit/tools/kit --json research status"
echo "  master-kit/tools/kit --json math status"
