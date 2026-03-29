# Orchestration-Kit (Deprecated)

This repo is retired as a live orchestration surface.

Do not start new work here. Do not use the embedded kits as the source of truth. New swarm work must use:

- `/Users/brandonbell/LOCAL_DEV/kenoma-kbus`
- `/Users/brandonbell/LOCAL_DEV/kenoma-oracle-pod`
- `/Users/brandonbell/LOCAL_DEV/tdd-kit`
- `/Users/brandonbell/LOCAL_DEV/research-kit`
- `/Users/brandonbell/LOCAL_DEV/mathematics-kit`

## Allowed Use

Only use this repo for:

- archival reference
- migration support
- emergency maintenance of legacy workflows

## Runtime Guard

The legacy entrypoints in this repo are blocked by default.

If you intentionally need a historical workflow, opt in first:

```bash
export ORCHESTRATION_KIT_ALLOW_LEGACY=1
```

That override is not permission to resume feature development here. It only disables the fail-closed deprecation guard for temporary legacy work.

## Agent Rules

- Treat this repo as deprecated infrastructure, not as the active swarm runtime.
- Do not tell users to run `install.sh`, `tools/kit`, or the embedded kit scripts as the default path.
- Redirect users to the sibling repos listed above.
- Do not add new features here unless the change is strictly required to preserve or retire legacy behavior.
- Prefer deprecation, migration, and compatibility edits over new orchestration logic.
