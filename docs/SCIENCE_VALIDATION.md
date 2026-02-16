# Science Validation Harness

`tools/science-validation` executes a high-trust validation workflow across:

- all three kits (`tdd`, `research`, `math`)
- interop request/pump branching and cycle routing
- two failure classes (`blocked`, `failed`)
- dashboard multi-project indexing and graph visibility checks

The harness writes a JSON evidence report and exits non-zero if required checks fail.

## Usage

Live profile (default):

```bash
tools/science-validation --profile live --agent-bin claude --reset
```

Deterministic profile (for local plumbing checks):

```bash
tools/science-validation --profile deterministic --reset
```

Useful options:

- `--project-a /tmp/orchestration-kit-science-a`
- `--project-b /tmp/orchestration-kit-science-b`
- `--dashboard-home /tmp/orchestration-kit-dashboard-science`
- `--dashboard-port 7340`
- `--output /tmp/orchestration-kit-science-validation-report.json`
- `--reset` (removes existing project directories before recreating)

## What It Verifies

1. Non-trivial orchestration graph:
   - parent run
   - two branch requests
   - cycle hop back to TDD
2. Failure classes:
   - controlled `blocked` outcome (live profile)
   - controlled `failed` outcome
3. Accountability artifacts:
   - run capsules/manifests/logs/events for each run id
   - capsule/manifest validator pass
   - explicit guardrail probe via orchestrator hook
4. Dashboard behavior:
   - project registration for two projects
   - global index + project-scoped reindex
   - API checks on `/api/projects`, `/api/summary`, `/api/graph`, `/api/runs`, `/api/run`

## Report Structure

The output JSON includes:

- `status`
- `checks[]` with `name`, `ok`, and `details`
- `orchestration` request/run payloads
- `dashboard` index/API verification summary
- `paths` and prerequisite tool locations

Use the report as the primary audit artifact for science/math/ML orchestration readiness.
