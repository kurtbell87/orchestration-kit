# Claude Research Kit

Structured experiment workflow for Claude Code with anti-confirmation-bias guardrails.

## Monorepo Context

This kit can run standalone, but in this repository it is generally orchestrated through Orchestration-Kit.

- Repository overview: `../README.md`
- Canonical PRD: `../docs/PRD_ORCHESTRATION_KIT.md`

## Phase Model

| Phase | Command | Purpose |
|---|---|---|
| SURVEY | `./experiment.sh survey <question>` | Review prior work and current codebase context |
| FRAME | `./experiment.sh frame <spec-file>` | Define hypothesis and success criteria |
| RUN | `./experiment.sh run <spec-file>` | Execute protocol with locked spec |
| READ | `./experiment.sh read <spec-file>` | Evaluate outcomes against pre-committed criteria |
| LOG | `./experiment.sh log <spec-file>` | Commit results and update research log |
| CYCLE | `./experiment.sh cycle <spec-file>` | FRAME -> RUN -> READ -> LOG |
| FULL | `./experiment.sh full <question> <spec-file>` | SURVEY -> FRAME -> RUN -> READ -> LOG |

Additional commands:

- `./experiment.sh status`
- `./experiment.sh program [--max-cycles N] [--dry-run]`
- `./experiment.sh synthesize [reason]`
- `./experiment.sh complete-handoff`
- `./experiment.sh validate-handoff`
- `./experiment.sh watch [phase] [--resolve]`

## Quick Start

```bash
# Configure
$EDITOR experiment.sh
$EDITOR QUESTIONS.md

# One cycle
./experiment.sh survey "Does X improve Y?"
./experiment.sh frame experiments/exp-001.md
./experiment.sh run experiments/exp-001.md
./experiment.sh read experiments/exp-001.md
./experiment.sh log experiments/exp-001.md
```

## Watch and Logs

```bash
./experiment.sh watch run
./experiment.sh watch read
./experiment.sh watch survey --resolve
```

- Phase logs: `$EXP_LOG_DIR/{phase}.log` (default `/tmp/exp-<project>/`)
- Stdout is intentionally compact; detailed traces remain on disk

## Guardrails

Defense in depth:

1. Phase-specific prompts
2. OS locking for spec/metrics during critical phases
3. Pre-tool-use hook preventing bypasses and post-hoc criteria drift

READ verdicts are explicit:

- `CONFIRMED`
- `REFUTED`
- `INCONCLUSIVE`

## Configuration

Set in `experiment.sh`:

```bash
TRAIN_CMD="python train.py"
EVAL_CMD="python eval.py"
TEST_CMD="pytest"
SRC_DIR="src"
DATA_DIR="data"
CONFIGS_DIR="configs"
MAX_GPU_HOURS="4"
MAX_RUNS="10"
```

Program-mode controls include:

- `MAX_PROGRAM_CYCLES`
- `MAX_PROGRAM_GPU_HOURS`
- `INCONCLUSIVE_THRESHOLD`

## Aliases (Optional)

```bash
source experiment-aliases.sh

exp-survey "question"
exp-frame experiments/exp-001.md
exp-run experiments/exp-001.md
exp-read experiments/exp-001.md
exp-log experiments/exp-001.md
exp-cycle experiments/exp-001.md
exp-full "question" experiments/exp-001.md
exp-program
exp-synthesize
exp-status
exp-unlock
```

## Standalone Install

For non-monorepo projects only:

```bash
/path/to/research-kit/install.sh
```

In this monorepo, use `tools/bootstrap` at repo root instead.

## Troubleshooting

- Hook not firing: verify `.claude/settings.json` and executable hook permissions.
- Locked files after interruption: run `exp-unlock`.
- Program mode paused: inspect `HANDOFF.md`, resolve requested infra work, then run `./experiment.sh complete-handoff`.
