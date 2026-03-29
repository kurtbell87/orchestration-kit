# Deprecation Notice

## Effective Status

As of `2026-03-29`, `orchestration-kit` is deprecated as an active runtime and development surface.

This repo remains available for archival reference and narrowly scoped legacy maintenance, but it is no longer the supported way to run swarms or kits.

## Why

The current stack has clearer boundaries and stronger enforcement:

- `kenoma-kbus` owns coordination transport and runtime launchers
- `kenoma-oracle-pod` owns recursive PI/oracle behavior
- standalone sibling kits own phase discipline and enforcement
- Codex-safe guards now live in the standalone kits, not in this monorepo

Keeping `orchestration-kit` active in parallel would create two competing control surfaces and two conflicting sources of truth for the kits.

## Source Of Truth

Use these repos instead:

- `/Users/brandonbell/LOCAL_DEV/kenoma-kbus`
- `/Users/brandonbell/LOCAL_DEV/kenoma-oracle-pod`
- `/Users/brandonbell/LOCAL_DEV/tdd-kit`
- `/Users/brandonbell/LOCAL_DEV/research-kit`
- `/Users/brandonbell/LOCAL_DEV/mathematics-kit`

The embedded kit directories in this repo are deprecated copies.

## Runtime Policy

The following `orchestration-kit` entrypoints are blocked by default:

- `install.sh`
- `tools/kit`
- `tools/pump`
- `tools/dashboard`
- `tools/mcp-serve`
- `tools/worktree-init`

They can only be used by explicitly opting into legacy mode:

```bash
export ORCHESTRATION_KIT_ALLOW_LEGACY=1
```

That override is for emergency maintenance, migration, or archival validation only.

## Migration Rules

Use these replacements:

- monorepo orchestration -> `kenoma-kbus` plus sibling kits
- embedded TDD/Research/Math kit copies -> standalone sibling kit repos
- orchestration-monorepo install/bootstrap -> swarm bootstrap through `kenoma-kbus`
- interop queue/pump workflow -> `kbus`-coordinated handoffs

## Maintenance Policy

Allowed:

- deprecation and migration docs
- emergency fixes needed to inspect or recover historical runs
- compatibility shims that help users leave this repo behind

Disallowed:

- new feature development
- treating embedded kits as canonical
- building new swarm workflows on top of this monorepo
