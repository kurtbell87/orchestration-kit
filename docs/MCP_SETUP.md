# MCP Setup (Claude Code + Codex CLI)

This guide configures the local Master-Kit MCP server for both Claude Code and Codex CLI.

## Prerequisites

- macOS or Linux shell environment
- `python3` available
- repository checked out locally
- existing `tools/kit`, `tools/pump`, and `tools/query-log`

## 1) Run installer (recommended)

From repo root:

```bash
./install.sh
source .master-kit.env
```

This bootstraps tooling, creates an MCP token, and writes `.master-kit.env`.

## 2) Set environment variables manually (alternative)

From repo root:

```bash
export MASTER_KIT_ROOT="$(pwd)"
export MASTER_KIT_MCP_HOST="127.0.0.1"
export MASTER_KIT_MCP_PORT="7337"
export MASTER_KIT_MCP_MAX_OUTPUT_BYTES="32000"
export MASTER_KIT_MCP_TOKEN="$(tools/mcp-token)"
```

Persist these in your shell profile if needed.

## 3) Start MCP server

```bash
tools/mcp-serve
```

Expected startup line includes:

- `master-kit mcp ready`
- URL `http://127.0.0.1:7337/mcp`

## 4) Configure Claude Code

Use project-scoped config or CLI registration (depending on your Claude version).

### Option A: project `.mcp.json`

```json
{
  "mcpServers": {
    "master-kit": {
      "transport": "http",
      "url": "http://127.0.0.1:7337/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN_HERE"
      }
    }
  }
}
```

### Option B: Claude CLI

```bash
claude mcp add --scope project master-kit http://127.0.0.1:7337/mcp \
  --header "Authorization: Bearer $MASTER_KIT_MCP_TOKEN"
```

## 5) Configure Codex CLI

Edit `~/.codex/config.toml`:

```toml
[mcp_servers.master_kit]
url = "http://127.0.0.1:7337/mcp"

[mcp_servers.master_kit.headers]
Authorization = "Bearer YOUR_TOKEN_HERE"
```

Replace `YOUR_TOKEN_HERE` with your token value.

## 6) Validate connectivity

Use either Claude or Codex MCP client to call:

- `master.run` with `{"kit":"research","action":"status"}`

Verify result contains pointers such as:

- `run_id`
- `capsule_path`
- `manifest_path`
- `events_path`

## 7) Cross-spawn workers

Wrappers execute one request and preserve MCP env:

```bash
tools/spawn-claude-worker <request_id>
tools/spawn-codex-worker <request_id>
```

If direct CLI MCP invocation flags are unavailable, wrappers fallback to:

```bash
tools/pump --once --request <request_id> --json
```

## 8) Stop server

Foreground run:

- `Ctrl+C`

Background example:

```bash
nohup tools/mcp-serve > runs/mcp-logs/server.log 2>&1 &
echo $! > runs/mcp-logs/server.pid
```

Stop:

```bash
kill "$(cat runs/mcp-logs/server.pid)"
```

## Security Notes

- Keep token enabled even on localhost.
- Keep `.mcp-token` and `.mcp.json` uncommitted.
- Rotate token when needed:

```bash
tools/mcp-token --rotate
```
