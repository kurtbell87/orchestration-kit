# Claude Mathematics Kit v2 — Product Requirements Document

## Status: READY FOR IMPLEMENTATION
## Spec Reference: claude-math-kit-v2-spec.md (v1.2)
## Target: Mathlib-dependent projects, filtered probability space formalization

---

## 1. Overview

This PRD translates the v2 improvement spec into concrete implementation tasks across the existing codebase. Six requirement groups are addressed, ordered by implementation priority. Each section specifies exactly which files change, what changes, and how to validate.

The codebase consists of:

| File | Role |
|------|------|
| `install.sh` | One-time installer into a Lean4 project |
| `math.sh` | Orchestrator — runs phases, manages locks, revision loops, program mode |
| `.claude/hooks/pre-tool-use.sh` | Hook enforcement for phase constraints |
| `.claude/prompts/math-survey.md` | SURVEY phase system prompt |
| `.claude/prompts/math-specify.md` | SPECIFY phase system prompt |
| `.claude/prompts/math-construct.md` | CONSTRUCT phase system prompt |
| `.claude/prompts/math-formalize.md` | FORMALIZE phase system prompt |
| `.claude/prompts/math-prove.md` | PROVE phase system prompt |
| `.claude/prompts/math-audit.md` | AUDIT phase system prompt |
| `scripts/math-watch.py` | Live monitoring dashboard |
| `templates/*` | Spec template, CONSTRUCTIONS.md, DOMAIN_CONTEXT.md, etc. |
| `README.md` | Documentation |

---

## 2. R3 — Mathlib Build Caching (Priority 1)

### 2.1 Problem

Every `lake build` during FORMALIZE and PROVE potentially recompiles the full Mathlib dependency tree. Without prebuilt oleans, this takes 20-40 minutes per invocation, making iterative proving impractical.

### 2.2 Changes

#### 2.2.1 `install.sh` — Mathlib detection and cache setup

**Add after the existing Mathlib dependency check (around the `grep -q "Mathlib"` block):**

A new function `setup_mathlib_cache()` that:

1. **Detects Mathlib dependency** (R3.1): Check both `lakefile.lean` and `lakefile.toml` for the string `mathlib` (case-insensitive). Set a flag `HAS_MATHLIB=true/false`.

2. **Toolchain version match check** (R3.3a): If `HAS_MATHLIB=true`:
   - Read the project's `lean-toolchain` file (should contain something like `leanprover/lean4:v4.x.0`)
   - Read Mathlib's pinned toolchain from `.lake/packages/mathlib/lean-toolchain` (after `lake update`)
   - Compare them. If they differ:
     ```
     ERROR: Toolchain mismatch detected.
       Project:  leanprover/lean4:v4.27.0
       Mathlib:  leanprover/lean4:v4.28.0
     
     Mathlib pins its toolchain version. Update your lean-toolchain to match:
       cp .lake/packages/mathlib/lean-toolchain ./lean-toolchain
     Then re-run install.sh.
     ```
     Exit 1 on mismatch.

3. **Fetch precompiled oleans** (R3.3): If `HAS_MATHLIB=true` and toolchain matches:
   ```bash
   echo "Fetching Mathlib precompiled oleans..."
   lake exe cache get 2>&1
   ```
   If `lake exe cache get` fails or isn't available, fall through to full build with a warning:
   ```
   WARNING: lake exe cache get failed. Falling back to full build.
   This will take 20-40 minutes.
   ```

4. **Build once to populate cache** (R3.2): After `cache get` (or instead of it if it fails), run `lake build` once. This is the existing behavior but should now be explicitly labeled:
   ```
   Building Lean4 project (populating olean cache)...
   This is a one-time cost. Subsequent builds will be incremental.
   ```

**Integration point:** This function is called after `lake update` succeeds and before the final "Done!" message. The existing `lake build` call at the end of install.sh should be absorbed into this function.

#### 2.2.2 `install.sh` — Never run `lake clean` (R3.4)

Add a comment near the top of the file:
```bash
# IMPORTANT: This script NEVER runs `lake clean`. Olean caches are precious
# with Mathlib. If the user needs a clean build, that's a manual operation.
```

This is documentation-only — the current script doesn't run `lake clean`, but making the invariant explicit prevents future regressions.

#### 2.2.3 `math.sh` — Validate build environment before builds (R3.5)

**Add a new helper function `validate_build_env()`:**

```bash
validate_build_env() {
  # Verify lake can resolve paths before attempting builds
  if ! lake env printPaths >/dev/null 2>&1; then
    echo -e "${RED}Error: 'lake env printPaths' failed.${NC}" >&2
    echo -e "The Lean4 toolchain may be misconfigured." >&2
    echo -e "Try: elan default leanprover/lean4:stable" >&2
    return 1
  fi
}
```

**Call `validate_build_env` at the start of:**
- `run_formalize()` — before the agent starts writing .lean files
- `run_prove()` — before the agent starts filling sorrys

If it fails, exit with a clear error rather than letting the agent burn turns against a broken toolchain.

#### 2.2.4 `math.sh` — Ensure `lake clean` is not available to agents (R3.4)

**In `.claude/hooks/pre-tool-use.sh`, add to the Bash universal blocks:**

```bash
if echo "$INPUT" | grep -qEi 'lake\s+clean'; then
  echo "BLOCKED: 'lake clean' is forbidden. Olean caches must be preserved." >&2
  exit 1
fi
```

### 2.3 Validation

| Test | Expected |
|------|----------|
| Run `install.sh` on a Mathlib project with correct toolchain | `lake exe cache get` runs, build completes, subsequent `lake build` takes <30s |
| Run `install.sh` with deliberate toolchain mismatch (edit `lean-toolchain` to wrong version) | Clear error message showing both versions, exit 1 |
| Run `lake exe cache get` when network is down | Falls back to full build with warning |
| Agent attempts `lake clean` during any phase | Hook blocks it |
| Run `math.sh formalize` on a project with broken elan | `validate_build_env` catches it before agent starts |

---

## 3. R1 — Mathlib-Aware Survey Phase (Priority 2)

### 3.1 Problem

The SURVEY phase prompt has no strategy for navigating Mathlib's large, namespaced source tree. The surveyor agent guesses at lemma names rather than reading actual source files.

### 3.2 Changes

#### 3.2.1 `scripts/mathlib-search.sh` — New utility script

Create a new file `scripts/mathlib-search.sh`:

```bash
#!/usr/bin/env bash
# mathlib-search.sh — Search Mathlib source for definitions, theorems, and typeclasses
#
# Usage:
#   ./scripts/mathlib-search.sh <query> [--defs] [--thms] [--instances] [--module <path>]
#
# Examples:
#   ./scripts/mathlib-search.sh "IsStoppingTime"
#   ./scripts/mathlib-search.sh "Filtration" --module MeasureTheory
#   ./scripts/mathlib-search.sh "condexp" --defs --thms
```

The script should:
1. Locate the Mathlib source tree (check `.lake/packages/mathlib/Mathlib/` and `lake-packages/mathlib/Mathlib/`)
2. Support search modes:
   - `--defs`: grep for `^def `, `^noncomputable def `, `^abbrev ` matching the query
   - `--thms`: grep for `^theorem `, `^lemma ` matching the query
   - `--instances`: grep for `^instance ` matching the query
   - Default (no flag): search all of the above
   - `--module <path>`: restrict search to `Mathlib/<path>/` subtree
3. Strip comments (lines starting with `--` or inside `/- -/`)
4. Output format: `Mathlib/Path/To/File.lean:42:  theorem foo_bar : ...`
5. Deduplicate and sort by file path

#### 3.2.2 `.claude/prompts/math-survey.md` — Expanded Mathlib navigation strategy (R1.1)

Replace the existing Process section with an expanded version that includes:

```markdown
## Mathlib Navigation Strategy

When surveying Mathlib for a domain, follow this systematic approach:

### Step 1: Identify root modules
Start from the relevant Mathlib source directories. Common starting points:
- `Mathlib/MeasureTheory/` — measure spaces, integration, probability
- `Mathlib/Probability/` — probability-specific constructions
- `Mathlib/Order/Filter/` — filters and filtrations
- `Mathlib/Topology/` — topological spaces, continuity
- `Mathlib/Analysis/` — real analysis, normed spaces
- `Mathlib/Algebra/` — algebraic structures
- `Mathlib/Data/` — concrete data types (Nat, Real, etc.)

### Step 2: Use mathlib-search
Run `./scripts/mathlib-search.sh` to find relevant definitions and theorems:
```
./scripts/mathlib-search.sh "IsStoppingTime" --module MeasureTheory
./scripts/mathlib-search.sh "Filtration" --defs
./scripts/mathlib-search.sh "condexp" --thms
```

### Step 3: Read module source files
For each relevant hit, read the actual .lean file to understand:
- The full type signature (not just the name)
- Required typeclass assumptions (e.g., `[MeasurableSpace α]`, `[TopologicalSpace α]`)
- Universe polymorphism annotations
- Which imports the module pulls in

### Step 4: Follow import chains
If a definition references types from other modules, trace those imports:
```bash
head -20 .lake/packages/mathlib/Mathlib/MeasureTheory/Measure/MeasureSpace.lean
```

### Step 5: Check for API gaps
Identify cases where:
- A definition exists but the lemma you need about it doesn't
- A lemma exists for `ℕ`-indexed objects but not `ℝ`-indexed ones
- A typeclass instance is missing (e.g., `ProbabilityMeasure` but no `FiniteMeasure` instance where needed)
- Universe parameters might conflict when composing types

Record these gaps explicitly — they determine whether the PROVE phase will need to build auxiliary lemmas.
```

#### 3.2.3 `.claude/prompts/math-survey.md` — Domain context file output (R1.2, R1.3)

Add to the Output Format section:

```markdown
## Domain Context Output
After surveying, produce content for DOMAIN_CONTEXT.md structured as:

### Concept → Mathlib Identifier Mappings
```
concept_name → MathLib.Full.Identifier.Name
  Module: Mathlib/Path/To/Module.lean
  Type: the full type signature
  Assumptions: [TypeClass1 α] [TypeClass2 β]
  Universe: universe u v
```

### Required Imports
List the exact `import` statements needed:
```
import Mathlib.MeasureTheory.Measure.MeasureSpace
import Mathlib.MeasureTheory.Stopping
```

### API Gaps
For each identified gap, record:
- What's missing
- What exists that's close
- Whether the gap is bridgeable (a few lines of glue) or substantial (needs a new development)
```

#### 3.2.4 `.claude/prompts/math-survey.md` — Mathlib source tree access (R1.4)

Add to the Hard Constraints section:

```markdown
- You HAVE read access to the local Mathlib source tree at `.lake/packages/mathlib/` (or `lake-packages/mathlib/`).
  Use this to read actual definitions and type signatures, not just guess at names.
- Use `./scripts/mathlib-search.sh` for efficient searches across the Mathlib tree (R1.5).
```

#### 3.2.5 `math.sh` — Pass Mathlib source path to surveyor

In `run_survey()`, add to the context section of the `--append-system-prompt`:

```
- Mathlib source: $(find .lake/packages/mathlib/Mathlib -maxdepth 0 -type d 2>/dev/null || find lake-packages/mathlib/Mathlib -maxdepth 0 -type d 2>/dev/null || echo 'not found')
- Mathlib search: ./scripts/mathlib-search.sh (use for targeted searches)
```

#### 3.2.6 `install.sh` — Install mathlib-search.sh

Add to the "Monitoring" section of install.sh:

```bash
install_executable "$KIT_DIR/scripts/mathlib-search.sh" "$TARGET_DIR/scripts/mathlib-search.sh"
```

### 3.3 Validation

| Test | Expected |
|------|----------|
| Run `mathlib-search.sh "IsStoppingTime"` on a Mathlib project | Returns file path + line number for `MeasureTheory.IsStoppingTime` |
| Run SURVEY targeting `MeasureTheory.IsStoppingTime` | Domain context file maps ≥5 identifiers (IsStoppingTime, Filtration, Adapted, measurableSet_le, etc.) |
| Survey identifies required imports | Exact import statements are listed |
| Survey identifies API gaps | At least one gap or "no gaps found" with evidence |

---

## 4. R5 — Theorem Dependency Chains (Priority 3)

### 4.1 Problem

The filtered probability space formalization is a sequence of 4 dependent theorems. The kit has no mechanism to express dependencies between constructions or ensure build order.

### 4.2 Changes

#### 4.2.1 `templates/CONSTRUCTIONS.md` — Add `depends_on` field (R5.1)

Update the template to include a Dependencies column and a dedicated Dependencies section:

```markdown
| Priority | Construction | Spec File | Status | Depends On | Notes |
|----------|-------------|-----------|--------|------------|-------|
| P1 | stopping_time_tau_plus | `specs/stopping-time-tau-plus.md` | Not started | — | |
| P2 | stopping_time_tau_minus | `specs/stopping-time-tau-minus.md` | Not started | — | |
| P3 | stopping_time_min | `specs/stopping-time-min.md` | Not started | P1, P2 | |
| P4 | hitting_event_measurable | `specs/hitting-event-measurable.md` | Not started | P3 | |
```

#### 4.2.2 `math.sh` — Topological sort in program mode (R5.2)

Replace `select_next_construction()` with a Python-based dependency resolver:

**New function `resolve_construction_order()`:**

```bash
resolve_construction_order() {
  python3 -c "
import re, sys, json
from collections import defaultdict, deque

try:
    with open('$CONSTRUCTIONS_FILE') as f:
        content = f.read()
except FileNotFoundError:
    sys.exit(1)

# Parse constructions
constructions = {}  # priority -> {name, spec, status, depends_on}
lines = content.split('\n')
for line in lines:
    cells = [c.strip() for c in line.split('|')]
    cells = [c for c in cells if c]
    if len(cells) < 5:
        continue
    priority = cells[0]
    if not re.match(r'^P\d+$', priority):
        continue
    depends = [d.strip() for d in cells[4].split(',') if d.strip() and d.strip() != '—']
    constructions[priority] = {
        'name': cells[1].strip('_ '),
        'spec': cells[2].strip('\` '),
        'status': cells[3].strip().lower(),
        'depends_on': depends,
    }

# Build adjacency list
graph = defaultdict(list)
in_degree = defaultdict(int)
for p, c in constructions.items():
    if p not in in_degree:
        in_degree[p] = 0
    for dep in c['depends_on']:
        graph[dep].append(p)
        in_degree[p] += 1

# Detect cycles
queue = deque([p for p in constructions if in_degree[p] == 0])
order = []
while queue:
    node = queue.popleft()
    order.append(node)
    for neighbor in graph[node]:
        in_degree[neighbor] -= 1
        if in_degree[neighbor] == 0:
            queue.append(neighbor)

if len(order) != len(constructions):
    cycle_nodes = [p for p in constructions if p not in order]
    print(json.dumps({'error': 'cycle', 'nodes': cycle_nodes}))
    sys.exit(1)

# Output ordered constructions, filtering to actionable ones
for p in order:
    c = constructions[p]
    if c['status'] in ('not started', 'specified', 'constructed', 'revision'):
        # Check if all dependencies are satisfied (audited or proved)
        deps_met = all(
            constructions.get(d, {}).get('status', '') in ('audited', 'proved')
            for d in c['depends_on']
        )
        blocked = not deps_met and c['depends_on']
        print(json.dumps({
            'priority': p,
            'name': c['name'],
            'spec': c['spec'],
            'status': c['status'],
            'depends_on': c['depends_on'],
            'blocked': blocked,
        }))
" 2>/dev/null
}
```

**Update `run_program()` to use this resolver:**

Instead of calling `select_next_construction()`, call `resolve_construction_order()` and process the first non-blocked entry. If a construction's dependencies aren't met, mark it `BLOCKED` (R5.4).

#### 4.2.3 `math.sh` — Import chaining for proved theorems (R5.3)

After a theorem's PROVE phase succeeds and AUDIT passes, the resulting .lean file should be importable by downstream constructions. Add logic to `run_log()` (or a new helper called after audit):

```bash
register_proved_theorem() {
  local spec_file="$1"
  local cid
  cid="$(construction_id_from_spec "$spec_file")"
  
  # Find the .lean files produced by this construction
  local lean_files
  lean_files=$(find_lean_files | grep -i "$cid" || true)
  
  # Record in DOMAIN_CONTEXT.md under a "## Proved & Available" section
  if [[ -n "$lean_files" ]]; then
    echo "" >> DOMAIN_CONTEXT.md
    echo "### Proved: $cid" >> DOMAIN_CONTEXT.md
    echo "Import with:" >> DOMAIN_CONTEXT.md
    for f in $lean_files; do
      # Convert file path to Lean import path
      local import_path
      import_path=$(echo "$f" | sed 's|/|.|g' | sed 's|\.lean$||' | sed 's|^\./||')
      echo "  \`import $import_path\`" >> DOMAIN_CONTEXT.md
    done
  fi
}
```

Call this at the end of `run_audit()` when the audit passes (no REVISION.md created).

#### 4.2.4 `math.sh` — Block downstream on failure (R5.4)

In `run_program()`, when a construction exhausts its revision cycles:

```bash
# Mark this construction as BLOCKED
update_construction_status "$spec_file" "Blocked"

# Mark all downstream dependents as BLOCKED
mark_downstream_blocked "$spec_file"
```

**New helper `mark_downstream_blocked()`** that reads CONSTRUCTIONS.md, finds all constructions that transitively depend on the failed one, and marks them BLOCKED with a note.

#### 4.2.5 `math.sh` — `--resume` flag for program mode (R5.5)

Add `--resume` flag parsing to `run_program()`:

```bash
run_program() {
  local max_cycles="$MAX_PROGRAM_CYCLES"
  local resume=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --max-cycles) max_cycles="$2"; shift 2 ;;
      --resume)     resume=true; shift ;;
      *)            echo -e "${RED}Unknown argument: $1${NC}" >&2; return 1 ;;
    esac
  done

  if $resume; then
    echo -e "${YELLOW}Resuming from first FAILED/BLOCKED construction...${NC}"
    # Reset FAILED/BLOCKED status to "Revision" for retry
    # But preserve negative knowledge annotations in DOMAIN_CONTEXT.md
    # (R5.5: annotations from failed run persist on resume)
    reset_failed_constructions  # new helper
  fi
  # ... rest of run_program
}
```

**`reset_failed_constructions()`** changes status of FAILED/BLOCKED entries back to their pre-failure state ("Not started" or "Revision") but explicitly does NOT touch DOMAIN_CONTEXT.md — negative knowledge from R6.5 persists.

#### 4.2.6 Diamond dependency handling (R5.2 addition)

The topological sort in 4.2.2 naturally handles diamonds — topological sort of a DAG linearizes correctly regardless of diamond structure. The import deduplication is handled by Lean itself (duplicate imports are idempotent). No additional code needed, but add a test case.

### 4.3 Validation

| Test | Expected |
|------|----------|
| Define chain A → B → C in CONSTRUCTIONS.md, run program mode | Executes in order A, B, C |
| Make B fail (exhaust revisions) | C is marked BLOCKED, skipped |
| Run `--resume` after fixing B's spec | Restarts from B, C follows. Negative knowledge preserved. |
| Define diamond A → B, A → C, B+C → D | A executes first, then B and C (in priority order), then D. No duplicate import errors. |
| Introduce a cycle (A depends on C, C depends on A) | Detected, error message, exit 1 |

---

## 5. R2 — Revision Loop Hardening (Priority 4)

### 5.1 Problem

The v1 run required 0 revision cycles. The revision mechanism exists but is untested and has no error classification, no cycle budget per theorem, and no oscillation detection.

### 5.2 Changes

#### 5.2.1 `scripts/lean-error-classify.sh` — New error classifier (R2.1)

Create a new script that takes `lake build` stderr on stdin and outputs a classification:

```bash
#!/usr/bin/env bash
# lean-error-classify.sh — Classify Lean4 build errors
#
# Usage: lake build 2>&1 | ./scripts/lean-error-classify.sh
#
# Output: one line per error, format: CLASS|FILE|LINE|SUMMARY
# Classes: TYPE_MISMATCH, UNKNOWN_IDENT, TACTIC_FAIL, TIMEOUT, UNIVERSE_INCOMPAT, OTHER

# Read stdin, parse error blocks, classify each
```

Classification logic:
- **UNIVERSE_INCOMPAT**: Look for `universe level`, `universe inconsistency`, `type mismatch` where the expected/found types differ only in universe parameters (`Type u` vs `Type v`), or `universe u` annotations in the error context. This class must be checked BEFORE TYPE_MISMATCH since universe errors often manifest as type mismatches.
- **TYPE_MISMATCH**: `type mismatch` in error text (after filtering out universe cases)
- **UNKNOWN_IDENT**: `unknown identifier`, `unknown constant`
- **TACTIC_FAIL**: `tactic '...' failed`, `unsolved goals`, `no goals to close`
- **TIMEOUT**: `deterministic timeout`, `maximum recursion depth`, `(timeout)`
- **OTHER**: anything else

#### 5.2.2 `.claude/prompts/math-prove.md` — Revision with classified errors (R2.2)

Add a section to the PROVE prompt:

```markdown
## Error Classification
When `lake build` fails, classify the error before attempting a fix:

1. Run: `lake build 2>&1 | ./scripts/lean-error-classify.sh`
2. Read the classification and apply the appropriate strategy:
   - **TYPE_MISMATCH**: Wrong lemma or missing coercion. Check the expected vs found types. Try a different lemma or add an explicit type annotation.
   - **UNKNOWN_IDENT**: Missing import or typo. Check DOMAIN_CONTEXT.md for the correct identifier. Add the import if missing.
   - **TACTIC_FAIL**: The tactic can't close the goal. Read the goal state, try a different tactic approach.
   - **TIMEOUT**: Proof term too large or search space explosion. Simplify the proof, break it into lemmas, or use more targeted tactics (e.g., `simp only [...]` instead of `simp`).
   - **UNIVERSE_INCOMPAT**: Universe unification failure. Do NOT treat this as a wrong-lemma problem. Check universe parameters on the types involved. Try explicit `Universe.{u}` annotations. Check if you need `ULift` or universe-polymorphic variants of lemmas.
```

#### 5.2.3 `math.sh` — Per-theorem revision budget (R2.3)

Currently, `MAX_REVISIONS` is a global limit for the entire construction. Add a per-theorem concept:

In `run_prove()`, the agent is already instructed to create REVISION.md after 3+ failed attempts. Strengthen this by adding to the PROVE prompt context:

```
- Max attempts per theorem: 5 (if you cannot prove a single theorem after 5 different tactic strategies, create REVISION.md)
```

The `run_full()` revision loop already handles REVISION.md. The change is making the per-theorem budget explicit in the prompt.

#### 5.2.4 `.claude/hooks/pre-tool-use.sh` — Detect spec modification during revision (R2.4)

The existing PROVE phase hook blocks spec writes. This is already implemented. Verify it also blocks theorem signature changes by adding a check:

In the `prove)` case, add logic that detects if an Edit to a .lean file modifies a line matching `^theorem |^lemma |^def |^structure |^inductive |^instance `:

```bash
prove)
  # ... existing checks ...
  if [[ "$TOOL" == "Edit" || "$TOOL" == "MultiEdit" ]]; then
    if echo "$INPUT" | grep -qE "$LEAN_PATTERN"; then
      # Check if the edit modifies a theorem signature
      OLD_STR=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('old_string',''))" 2>/dev/null || true)
      if echo "$OLD_STR" | grep -qE '^\s*(theorem|lemma|def|structure|inductive)\s+'; then
        echo "BLOCKED: PROVE phase cannot modify theorem signatures or definitions." >&2
        echo "   Only proof bodies (sorry replacements) are allowed." >&2
        echo "   If the statement is wrong, create REVISION.md." >&2
        exit 1
      fi
    fi
  fi
  ;;
```

**Important caveat:** This is a heuristic. The `old_string` in an Edit might span multiple lines and include both the signature and the proof body. The check should only block if the `old_string` starts with a signature keyword. If the old_string includes `sorry` (i.e., it's replacing the sorry in `theorem foo : P := by sorry`), that's fine — the agent is replacing the proof body, not the signature. Refine the check:

```bash
# Only block if editing a signature line AND the old_string does NOT contain sorry
if echo "$OLD_STR" | grep -qE '^\s*(theorem|lemma|def|structure|inductive)\s+' && \
   ! echo "$OLD_STR" | grep -qE '\bsorry\b'; then
  echo "BLOCKED: ..." >&2
  exit 1
fi
```

#### 5.2.5 `math.sh` / `math-watch.py` — Revision metrics (R2.5)

In `run_full()`, track revision metadata:

```bash
# At the start of run_full
local revision_log="$RESULTS_DIR/revision-metrics.json"
echo '{"revisions":[]}' > "$revision_log"

# On each revision cycle
python3 -c "
import json, datetime
with open('$revision_log') as f:
    data = json.load(f)
data['revisions'].append({
    'cycle': $revision_count,
    'restart_from': '$restart_from',
    'timestamp': datetime.datetime.now().isoformat(),
})
with open('$revision_log', 'w') as f:
    json.dump(data, f, indent=2)
"
```

In `math-watch.py`, add revision tracking to `AgentState`:
```python
self.revision_cycles = 0
self.error_classes = []  # list of classified errors
```

### 5.3 Validation

| Test | Expected |
|------|----------|
| Inject wrong lemma application, run PROVE | Error classified as TYPE_MISMATCH, revision prompt suggests different lemma |
| Inject universe inconsistency (compose MeasurableSpace across mismatched universes) | Classified as UNIVERSE_INCOMPAT, NOT TYPE_MISMATCH |
| Agent attempts to modify theorem signature during PROVE | Hook blocks with clear message |
| Agent exhausts 5 attempts on one theorem | Creates REVISION.md, pipeline continues |
| Revision metrics recorded after cycle | `revision-metrics.json` contains cycle count and error class |

---

## 6. R4 — Wall-Clock and Resource Metrics (Priority 5)

### 6.1 Problem

The metrics table records cost and agent turns but not timing. On a flat-rate subscription, time is the binding constraint.

### 6.2 Changes

#### 6.2.1 `math.sh` — Phase timing (R4.1, R4.2)

Wrap each phase runner with timing:

```bash
# Add at top of math.sh
phase_start_time=""

start_phase_timer() {
  phase_start_time=$(date +%s)
}

end_phase_timer() {
  local phase_name="$1"
  local end_time=$(date +%s)
  local elapsed=$((end_time - phase_start_time))
  echo -e "${CYAN}Phase $phase_name completed in ${elapsed}s${NC}"
  
  # Append to metrics file
  local metrics_file="${RESULTS_DIR}/metrics.jsonl"
  echo "{\"phase\":\"$phase_name\",\"elapsed_seconds\":$elapsed,\"timestamp\":\"$(date -Iseconds)\"}" >> "$metrics_file"
}
```

Add `start_phase_timer` at the beginning and `end_phase_timer "PHASE_NAME"` at the end of each `run_*()` function.

#### 6.2.2 `math.sh` — Separate lake build timing from agent turn timing (R4.3)

This is harder to instrument from the orchestrator level since `lake build` runs inside Claude Code agent turns. The practical approach:

1. In the PROVE and FORMALIZE prompts, instruct the agent to print timing markers:
   ```
   Before running lake build, print: "LAKE_BUILD_START"
   After lake build completes, print: "LAKE_BUILD_END"
   ```

2. In `math-watch.py`, parse these markers to track lake build duration separately:
   ```python
   self.lake_build_total_seconds = 0
   self.agent_think_total_seconds = 0
   ```

#### 6.2.3 `math-watch.py` — Display elapsed time per phase (R4.4)

Update `print_header()` to include phase elapsed time. This is already partially implemented (the `elapsed` property exists). Ensure it displays in the header bar and updates live.

#### 6.2.4 `math.sh` — Final metrics summary table (R4.2)

At the end of `run_full()`, print a summary:

```bash
print_final_metrics() {
  local metrics_file="${RESULTS_DIR}/metrics.jsonl"
  if [[ ! -f "$metrics_file" ]]; then return; fi
  
  echo ""
  echo -e "${BOLD}Pipeline Metrics${NC}"
  echo "┌──────────────┬───────┬──────────┬───────────┬────────┐"
  echo "│ Phase        │ Turns │ Duration │ Revisions │ Status │"
  echo "├──────────────┼───────┼──────────┼───────────┼────────┤"
  # Parse metrics.jsonl and format table
  python3 -c "
import json
with open('$metrics_file') as f:
    for line in f:
        d = json.loads(line)
        phase = d['phase'].upper().ljust(12)
        elapsed = f\"{d['elapsed_seconds']}s\".rjust(8)
        print(f'│ {phase} │   ?   │ {elapsed} │     ?     │  ?     │')
"
  echo "└──────────────┴───────┴──────────┴───────────┴────────┘"
}
```

(The turns and revisions columns require additional instrumentation from the agent log — this is a best-effort implementation that can be refined.)

### 6.3 Validation

| Test | Expected |
|------|----------|
| Run full pipeline | Each phase prints elapsed time on completion |
| Check `metrics.jsonl` after run | Contains one entry per phase with elapsed_seconds |
| `math-watch.py` displays elapsed time | Header bar shows running elapsed per phase |

---

## 7. R6 — Context Window Management (Priority 6)

### 7.1 Problem

Lean error messages with Mathlib references are verbose. The prover agent's context fills with type mismatch errors referencing deeply nested types, reducing reasoning capacity.

### 7.2 Changes

#### 7.2.1 `scripts/lean-error-summarize.sh` — New error summarizer (R6.1)

Create a new script:

```bash
#!/usr/bin/env bash
# lean-error-summarize.sh — Condense Lean4 build errors for agent consumption
#
# Usage: lake build 2>&1 | ./scripts/lean-error-summarize.sh
#
# Strips Mathlib-internal type expansions beyond depth 2, extracts goal state,
# groups errors by file:line, and produces a compact summary.
```

Implementation:
1. Parse error blocks (delimited by `error:` and blank lines or next `error:`)
2. For each error block:
   - Extract file path and line number
   - Extract the error class (first line after location)
   - If type expansion depth > 2 (heuristic: count nested `@` or `.` segments), truncate with `...`
   - If goal state is present (`⊢` line), extract it
3. Output format:
   ```
   [TYPE_MISMATCH] MyFile.lean:42
     Expected: MeasurableSet s
     Found:    MeasurableSet (f ⁻¹' s)
     Goal: ⊢ IsStoppingTime τ
   
   [TACTIC_FAIL] MyFile.lean:58
     tactic 'omega' failed
     Goal: ⊢ ∀ n, τ n ≤ τ' n
   ```
4. Cap output at 40 lines. If more, print count of remaining errors.

#### 7.2.2 `.claude/prompts/math-prove.md` — Use summarizer before raw errors (R6.2)

Add to the Process section:

```markdown
## Handling Build Errors

When `lake build` fails:
1. **First**: Run `lake build 2>&1 | ./scripts/lean-error-summarize.sh` to get a condensed view
2. **If the summary is insufficient**: Read the raw error output
3. **If the error is opaque**: Generate a minimal failing example (see below)

## Minimal Failing Examples (MWE)

When an error is hard to diagnose, create a minimal reproducer in a scratch file:

1. Create `scratch/MWE.lean`
2. Copy ONLY the failing definition/theorem and its minimal imports
3. Reduce the proof to the smallest term that still produces the error
4. Run `lake build scratch/MWE.lean` (or `lake env lean scratch/MWE.lean`)
5. The error on a 5-line file is far more readable than on a 200-line file

This is what experienced Lean users do when asking for help. It forces you to isolate the actual issue.
```

#### 7.2.3 `.claude/prompts/math-prove.md` — Oscillation detection (R6.3)

Add to the Process section:

```markdown
## Avoiding Oscillation

Track your proof attempts. If you see the same error twice in a row (same error type, same line, same failing term), you are oscillating. Do NOT try a third variation of the same approach.

Instead:
1. Stop and re-read the construction document and DOMAIN_CONTEXT.md
2. Check DOMAIN_CONTEXT.md for "DOES NOT APPLY" annotations
3. List all approaches you've tried so far
4. Choose a fundamentally different strategy (different tactic, different lemma family, different proof structure)
```

#### 7.2.4 `.claude/prompts/math-prove.md` — Negative knowledge accumulation (R6.5)

Add:

```markdown
## Recording Failed Approaches

When you discover that a Mathlib lemma doesn't apply (wrong typeclass assumptions, universe conflict, etc.), record it in DOMAIN_CONTEXT.md under a `## DOES NOT APPLY` section:

```
## DOES NOT APPLY
- MeasureTheory.StronglyMeasurable.integral_condexp: requires [TopologicalSpace α], our α is bare ℕ → ℝ
- MeasureTheory.Stopping.isStoppingTime_min: only for ℕ-indexed filtrations, we need ℝ-indexed
```

This prevents future revision cycles from re-attempting known-bad approaches.

**IMPORTANT**: You may ONLY append to the `## DOES NOT APPLY` section of DOMAIN_CONTEXT.md. Do not modify any other section.
```

#### 7.2.5 `.claude/hooks/pre-tool-use.sh` — Allow DOMAIN_CONTEXT.md append during PROVE (R6.5)

Currently the PROVE phase locks DOMAIN_CONTEXT.md (via `lock_spec`). Modify `lock_spec()` to NOT lock DOMAIN_CONTEXT.md, or add a separate mechanism.

**Preferred approach:** Don't lock DOMAIN_CONTEXT.md during PROVE. Instead, add a hook check that only allows appending to the `## DOES NOT APPLY` section:

In the `prove)` case of `pre-tool-use.sh`:

```bash
# Allow edits to DOMAIN_CONTEXT.md ONLY for appending to DOES NOT APPLY section
if echo "$INPUT" | grep -qE 'DOMAIN_CONTEXT\.md'; then
  if [[ "$TOOL" == "Write" ]]; then
    echo "BLOCKED: Cannot overwrite DOMAIN_CONTEXT.md during PROVE. Use Edit to append." >&2
    exit 1
  fi
  # Edit is allowed (the agent appends to the DOES NOT APPLY section)
fi
```

And in `math.sh`'s `lock_spec()`, skip locking DOMAIN_CONTEXT.md:

```bash
lock_spec() {
  local spec="$1"
  if [[ -f "$spec" ]]; then
    chmod 444 "$spec"
    echo -e "   ${YELLOW}locked:${NC} $spec"
  fi
  local cid
  cid="$(construction_id_from_spec "$spec")"
  for f in "$SPEC_DIR"/construction-"$cid"*; do
    if [[ -f "$f" ]]; then
      chmod 444 "$f"
      echo -e "   ${YELLOW}locked:${NC} $f"
    fi
  done
  # NOTE: DOMAIN_CONTEXT.md is NOT locked during PROVE.
  # The prover may append negative knowledge to the "DOES NOT APPLY" section.
  # Hook enforcement prevents modification of other sections.
}
```

And correspondingly update `unlock_spec()` to not try to unlock what wasn't locked.

#### 7.2.6 `scripts/lean-error-summarize.sh` and `scripts/lean-error-classify.sh` — Install

Add both to `install.sh`:

```bash
install_executable "$KIT_DIR/scripts/lean-error-summarize.sh" "$TARGET_DIR/scripts/lean-error-summarize.sh"
install_executable "$KIT_DIR/scripts/lean-error-classify.sh"  "$TARGET_DIR/scripts/lean-error-classify.sh"
```

### 7.3 Validation

| Test | Expected |
|------|----------|
| Trigger `MeasurableSpace.comap` type expansion error, pipe through summarizer | Readable summary under 20 lines |
| Two consecutive identical errors during PROVE | Agent detects oscillation, resets strategy |
| Prover discovers bad lemma, writes to DOMAIN_CONTEXT.md | Annotation appears in DOES NOT APPLY section |
| Prover attempts to overwrite DOMAIN_CONTEXT.md | Hook blocks Write, allows Edit |
| Next revision cycle reads DOMAIN_CONTEXT.md | Sees DOES NOT APPLY annotation, avoids re-attempting |
| Agent generates MWE for opaque error | Scratch file produces cleaner, shorter error output |

---

## 8. Documentation Updates

### 8.1 `README.md`

Add sections covering:
- Mathlib build caching (how it works, `lake exe cache get`, toolchain matching)
- Dependency chains in CONSTRUCTIONS.md (syntax, topological sort, blocked status)
- `--resume` flag for program mode
- New utility scripts: `mathlib-search.sh`, `lean-error-classify.sh`, `lean-error-summarize.sh`
- Updated metrics table format with timing columns
- Negative knowledge mechanism in DOMAIN_CONTEXT.md

### 8.2 `CLAUDE.md` (templates/CLAUDE.md.snippet)

Add:
- Note about `mathlib-search.sh` availability during SURVEY
- Error classification and summarization workflow during PROVE
- Negative knowledge protocol for DOMAIN_CONTEXT.md
- MWE generation strategy

---

## 9. New Files Summary

| File | Type | Description |
|------|------|-------------|
| `scripts/mathlib-search.sh` | Executable | Mathlib source tree search utility |
| `scripts/lean-error-classify.sh` | Executable | Lean4 error classifier (5 classes + OTHER) |
| `scripts/lean-error-summarize.sh` | Executable | Lean4 error summarizer (strips type expansions) |

## 10. Modified Files Summary

| File | Changes |
|------|---------|
| `install.sh` | Mathlib cache setup, toolchain check, new script installs |
| `math.sh` | Build validation, phase timing, dependency resolver, `--resume`, metrics, DOMAIN_CONTEXT unlock |
| `.claude/hooks/pre-tool-use.sh` | `lake clean` block, signature modification detection, DOMAIN_CONTEXT append policy |
| `.claude/prompts/math-survey.md` | Mathlib navigation strategy, domain context output format, source tree access |
| `.claude/prompts/math-prove.md` | Error classification workflow, MWE generation, oscillation detection, negative knowledge |
| `scripts/math-watch.py` | Phase timing display, revision tracking, error class tracking |
| `templates/CONSTRUCTIONS.md` | Depends On column |
| `templates/DOMAIN_CONTEXT.md` | DOES NOT APPLY section |
| `templates/CLAUDE.md.snippet` | New workflow documentation |
| `README.md` | Comprehensive updates |

---

## 11. Implementation Order

Execute in this order. Each step should be a separate commit.

1. **R3: Build caching** — `install.sh`, `math.sh` (validate_build_env), hook (`lake clean` block)
2. **R1: Mathlib survey** — `mathlib-search.sh`, `math-survey.md`, `math.sh` (survey context)
3. **R5: Dependency chains** — `math.sh` (resolver, program mode, `--resume`), `CONSTRUCTIONS.md` template
4. **R2: Revision hardening** — `lean-error-classify.sh`, `math-prove.md`, hook (signature check), `math.sh` (metrics)
5. **R4: Timing metrics** — `math.sh` (timers), `math-watch.py` (display), `run_full` (summary table)
6. **R6: Context management** — `lean-error-summarize.sh`, `math-prove.md` (MWE, oscillation, negative knowledge), hook (DOMAIN_CONTEXT policy), `math.sh` (unlock policy)
7. **Documentation** — README.md, CLAUDE.md.snippet, DOMAIN_CONTEXT.md template

Each commit should be independently functional — no commit should break the existing pipeline.
