# Orchestration-Kit (Deprecated)

`orchestration-kit` is retired as a live orchestration path.

New swarm and kit work must use the standalone sibling repos under `~/LOCAL_DEV/`:

- `kenoma-kbus`
- `kenoma-oracle-pod`
- `tdd-kit`
- `research-kit`
- `mathematics-kit`

## Status

- This repo is archival reference material and legacy-maintenance infrastructure only.
- The embedded `tdd-kit/`, `research-kit/`, and `mathematics-kit/` copies are no longer the source of truth.
- Main entrypoints now fail closed unless `ORCHESTRATION_KIT_ALLOW_LEGACY=1` is set explicitly.
- No new features should be built on top of this monorepo.

See [DEPRECATED.md](DEPRECATED.md) for the migration contract and legacy policy.

## What Replaced It

The active stack is split by responsibility:

- `kenoma-kbus`: transport, coordination, launchers, and swarm runtime
- `kenoma-oracle-pod`: recursive PI/oracle coordination layer
- `tdd-kit`: red/green/refactor discipline with backend-specific enforcement
- `research-kit`: bounded research and experiment workflow
- `mathematics-kit`: theorem/proof workflow with protected-statement enforcement

This split is intentional. `orchestration-kit` tried to combine kits, dashboarding, MCP, and orchestration into one monorepo. The current architecture treats the sibling kits as first-class components and lets the swarm runtime mount them directly.

## Migration

Common replacements:

| Legacy path | Replacement |
|-------------|-------------|
| `./install.sh` | bootstrap `kenoma-kbus`, then mount sibling kits directly |
| `tools/kit ...` | use the live swarm path in `kenoma-kbus` |
| `tools/pump ...` | use `kbus`-mediated handoffs and swarm artifacts |
| `tools/dashboard ...` | use the live runtime visibility from the current swarm stack |
| embedded `tdd-kit/` | `~/LOCAL_DEV/tdd-kit` |
| embedded `research-kit/` | `~/LOCAL_DEV/research-kit` |
| embedded `mathematics-kit/` | `~/LOCAL_DEV/mathematics-kit` |

## Legacy Use

If you must inspect or temporarily run a historical workflow here, opt in explicitly:

```bash
export ORCHESTRATION_KIT_ALLOW_LEGACY=1
```

That override exists for emergency maintenance only. It is not the normal path.

## Repository Role

Keep this repo for:

- historical documentation
- forensic comparison against the newer swarm stack
- emergency maintenance of old runs or tooling

Do not use it as the canonical place to evolve the biologic swarm system.
