# Codex TDD Mode

The standard TDD workflow can run on Codex CLI with the same phase commands.

## One-off usage

```bash
TDD_AGENT_BIN=codex ./tdd.sh red docs/my-feature.md
TDD_AGENT_BIN=codex ./tdd.sh green
TDD_AGENT_BIN=codex ./tdd.sh refactor
TDD_AGENT_BIN=codex ./tdd.sh breadcrumbs docs/my-feature.md
TDD_AGENT_BIN=codex ./tdd.sh ship docs/my-feature.md
```

## Shell aliases

```bash
source ./tdd-aliases.sh

tddc-red docs/my-feature.md
tddc-green
tddc-refactor
tddc-breadcrumbs docs/my-feature.md
tddc-ship docs/my-feature.md
```

## Notes

- Installer writes both `.claude/prompts` and `.codex/prompts`.
- Codex mode prefers `.codex/prompts` automatically (or use `PROMPT_DIR` override).
- Keep `AGENTS.md` updated (install from `templates/AGENTS.md.snippet`) for Codex-first projects.
- `ship` runs the breadcrumbs phase before commit/PR so docs are always refreshed.
- Logs are still written to `$TDD_LOG_DIR` and summarized compactly on stdout.
- `CODEX_SANDBOX_NETWORK_DISABLED` defaults to `0`; if it is `1` (or `true`), Codex CLI cannot reach its API from this shell. Run from a non-isolated shell or switch to `TDD_AGENT_BIN=claude`.
