#!/usr/bin/env python3
"""HTTP and stdio MCP facade for orchestration-kit tools."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def cap_text_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    clipped = encoded[:limit]
    return clipped.decode("utf-8", errors="ignore")


def parse_json_tail(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("failed to parse JSON object from command output")


def rel_to(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def coerce_env(env_payload: Any) -> dict[str, str]:
    if env_payload is None:
        return {}
    if not isinstance(env_payload, dict):
        raise ValueError("env must be an object")

    clean: dict[str, str] = {}
    for key, value in env_payload.items():
        if not isinstance(key, str):
            raise ValueError("env keys must be strings")
        clean[key] = str(value)
    return clean


def require_str(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is required and must be a non-empty string")
    return value


def optional_list(payload: dict[str, Any], field: str) -> list[Any]:
    value = payload.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def request_timestamp_id() -> str:
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"rq-{ts}-{uuid.uuid4().hex[:6]}"


def _make_run_id() -> str:
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{uuid.uuid4().hex[:8]}"


class MCPToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerConfig:
    root: Path
    host: str
    port: int
    token: str
    max_output_bytes: int
    log_dir: Path
    transport: str = "http"
    dashboard_url: str = "http://127.0.0.1:7340"
    project_root: Path | None = None
    kit_state_dir: str | None = None


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    # --- Legacy orchestrator.* tools (backward-compatible) ---
    {
        "name": "orchestrator.run",
        "description": "Run a orchestration-kit action via tools/kit and return pointer-only paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kit": {"type": "string", "enum": ["tdd", "research", "math"]},
                "action": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "reasoning": {"type": "string", "description": "1-3 sentence justification for this dispatch"},
            },
            "required": ["kit", "action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "orchestrator.request_create",
        "description": "Create an interop request file via tools/kit request.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_kit": {"type": "string", "enum": ["tdd", "research", "math"]},
                "from_phase": {"type": "string"},
                "to_kit": {"type": "string", "enum": ["tdd", "research", "math"]},
                "action": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "run_id": {"type": "string"},
                "must_read": {"type": "array", "items": {"type": "string"}},
                "read_budget": {
                    "type": "object",
                    "properties": {
                        "max_files": {"type": "integer", "minimum": 1},
                        "max_total_bytes": {"type": "integer", "minimum": 1},
                        "allowed_paths": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": False,
                },
                "deliverables_expected": {"type": "array", "items": {"type": "string"}},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
                "reasoning": {"type": "string", "description": "1-3 sentence justification for this request"},
            },
            "required": ["from_kit", "to_kit", "action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "orchestrator.pump",
        "description": "Execute one request (by id or queue front) via tools/pump.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["once", "queue"]},
                "request_id": {"type": "string"},
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
    },
    {
        "name": "orchestrator.run_info",
        "description": "Return pointer summary for a run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "orchestrator.query_log",
        "description": "Return bounded log snippet via tools/query-log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "mode": {"type": "string", "enum": ["tail", "grep", "lean_summarize"]},
                "pattern": {"type": "string"},
                "n": {"type": "integer", "minimum": 1, "maximum": 10000},
            },
            "required": ["path", "mode"],
            "additionalProperties": False,
        },
    },
    # --- Kit execution tools (fire-and-forget) ---
    {
        "name": "kit.tdd",
        "description": "Run full TDD cycle (red/green/refactor/ship). Returns immediately with run_id. Poll kit.status or kit.runs for completion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec_path": {"type": "string", "description": "Path to TDD spec, e.g. .kit/docs/feature.md"},
            },
            "required": ["spec_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kit.research_cycle",
        "description": "Run research experiment cycle (frame/run/read/log) from a spec. Returns immediately with run_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec_path": {"type": "string", "description": "Path to experiment spec, e.g. .kit/experiments/exp-001.md"},
            },
            "required": ["spec_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kit.research_full",
        "description": "Run full research cycle including survey (survey/frame/run/read/log). Returns immediately with run_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Research question to investigate"},
                "spec_path": {"type": "string", "description": "Path to experiment spec"},
            },
            "required": ["question", "spec_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kit.research_program",
        "description": "Run auto-advancing research program (picks next question from QUESTIONS.md). Returns immediately with run_id.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "kit.math",
        "description": "Run full math cycle. Returns immediately with run_id. Poll kit.status or kit.runs for completion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec_path": {"type": "string", "description": "Path to math spec, e.g. .kit/specs/construction.md"},
            },
            "required": ["spec_path"],
            "additionalProperties": False,
        },
    },
    # --- Dashboard query tools (synchronous) ---
    {
        "name": "kit.status",
        "description": "Get dashboard summary: total/running/ok/failed run counts.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "kit.runs",
        "description": "List runs with optional filters (status, kit, phase, limit).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["ok", "failed", "running"], "description": "Filter by run status"},
                "kit": {"type": "string", "enum": ["tdd", "research", "math"], "description": "Filter by kit"},
                "phase": {"type": "string", "description": "Filter by phase name"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Max runs to return (default 50)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "kit.capsule",
        "description": "Get capsule preview for a run (30-line failure summary). Use after kit.runs shows a failure.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "The run ID to get capsule for"},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kit.research_status",
        "description": "Get research program status: experiments, questions, and program_state.json overview.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    # --- Process visibility tools ---
    {
        "name": "kit.active",
        "description": "List all background processes launched by this MCP server. Returns run_id, pid, status (running/ok/failed), and exit_code for each.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "kit.kill",
        "description": "Terminate a background process launched by this MCP server. Only operates on processes tracked in the active list (cannot kill arbitrary PIDs).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "The run ID of the process to terminate"},
                "signal": {
                    "type": "string",
                    "enum": ["SIGTERM", "SIGKILL"],
                    "description": "Signal to send (default: SIGTERM)",
                },
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kit.gc",
        "description": "Garbage-collect stale runs. Re-indexes from events.jsonl on disk, then marks any 'running' run whose PID is dead as failed (exit_code=137). Returns counts of cleaned runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, report stale runs without modifying the database (default: false)",
                },
            },
            "additionalProperties": False,
        },
    },
]


class MasterKitFacade:
    def __init__(self, config: ServerConfig):
        self.config = config
        self.root = config.root
        self._lock = threading.Lock()
        self._background: dict[str, subprocess.Popen[bytes]] = {}

    def _tool_path(self, name: str) -> Path:
        return self.root / "tools" / name

    def _run_cmd(
        self,
        cmd: list[str],
        *,
        extra_env: dict[str, str] | None = None,
        timeout_seconds: int = 900,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["ORCHESTRATION_KIT_ROOT"] = str(self.root)
        if self.config.project_root:
            env["PROJECT_ROOT"] = str(self.config.project_root)
        # Forward KIT_STATE_DIR so tools/kit resolves script paths correctly.
        kit_state_dir = os.getenv("KIT_STATE_DIR")
        if kit_state_dir:
            env["KIT_STATE_DIR"] = kit_state_dir
        if extra_env:
            env.update(extra_env)

        return subprocess.run(
            cmd,
            cwd=str(self.root),
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )

    # --- SQLite direct access (replaces fragile dashboard HTTP proxy) ---

    def _db_path(self) -> Path:
        raw = os.getenv("ORCHESTRATION_KIT_DASHBOARD_HOME")
        if raw:
            return Path(raw).expanduser().resolve() / "state.db"
        return Path.home() / ".orchestration-kit-dashboard" / "state.db"

    def _db_connect(self) -> sqlite3.Connection:
        db = self._db_path()
        if not db.is_file():
            # Trigger an index so the DB exists
            self._run_cmd(
                [str(self._tool_path("dashboard")), "index"],
                timeout_seconds=30,
            )
        conn = sqlite3.connect(str(db), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _db_query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = self._db_connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _db_query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        conn = self._db_connect()
        try:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _db_execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        conn = self._db_connect()
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def _reindex(self) -> None:
        """Re-index dashboard DB from events.jsonl files on disk."""
        try:
            self._run_cmd(
                [str(self._tool_path("dashboard")), "index"],
                timeout_seconds=60,
            )
        except Exception:
            pass  # best effort

    # --- Fire-and-forget background launcher ---

    def _launch_background(self, kit: str, action: str, args: list[str]) -> dict[str, Any]:
        run_id = _make_run_id()
        # CRITICAL: --run-id and --json must come BEFORE positional args.
        # tools/kit uses argparse.REMAINDER for phase_args, which swallows
        # everything after the positional args (kit, phase). Any options
        # placed after the positionals get consumed as phase_args, not parsed.
        cmd = [str(self._tool_path("kit")), "--json", "--run-id", run_id, kit, action, *args]
        env = os.environ.copy()
        env["ORCHESTRATION_KIT_ROOT"] = str(self.root)
        if self.config.project_root:
            env["PROJECT_ROOT"] = str(self.config.project_root)
        # Forward KIT_STATE_DIR so tools/kit resolves script paths correctly
        # (greenfield projects use ".kit", monorepo uses ".").
        if self.config.kit_state_dir:
            env["KIT_STATE_DIR"] = self.config.kit_state_dir

        # Capture output to a launch log for error visibility instead of
        # discarding to DEVNULL. If the subprocess crashes at startup, the
        # log file preserves the error for diagnosis.
        launch_log_dir = self.root / "runs" / "mcp-launches"
        launch_log_dir.mkdir(parents=True, exist_ok=True)
        launch_log = launch_log_dir / f"{run_id}.log"
        log_fh = open(launch_log, "w")  # noqa: SIM115

        proc = subprocess.Popen(
            cmd,
            cwd=str(self.root),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        log_fh.close()  # subprocess has inherited the fd
        self._background[run_id] = proc
        return {"run_id": run_id, "status": "launched", "launch_log": str(launch_log)}

    # --- Kit execution tool handlers (fire-and-forget) ---

    def _tool_kit_tdd(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._launch_background("tdd", "full", [require_str(payload, "spec_path")])

    def _tool_kit_research_cycle(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._launch_background("research", "cycle", [require_str(payload, "spec_path")])

    def _tool_kit_research_full(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._launch_background(
            "research", "full", [require_str(payload, "question"), require_str(payload, "spec_path")]
        )

    def _tool_kit_research_program(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._launch_background("research", "program", [])

    def _tool_kit_math(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._launch_background("math", "full", [require_str(payload, "spec_path")])

    # --- Dashboard query tool handlers (synchronous, direct SQLite) ---

    def _tool_kit_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        runs = self._db_query_one(
            """
            SELECT
              COUNT(*) AS total_runs,
              SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_runs,
              SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_runs,
              SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_runs
            FROM runs
            """
        ) or {}

        reqs = self._db_query_one(
            """
            SELECT
              COUNT(*) AS total_requests,
              SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_requests,
              SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_requests,
              SUM(CASE WHEN status = 'failed' OR status IS NULL THEN 1 ELSE 0 END) AS failed_requests
            FROM requests
            """
        ) or {}

        active_by_phase = self._db_query(
            """
            SELECT
              COALESCE(kit, 'unknown') AS kit,
              COALESCE(phase, 'unknown') AS phase,
              COUNT(*) AS count
            FROM runs
            WHERE status = 'running'
            GROUP BY COALESCE(kit, 'unknown'), COALESCE(phase, 'unknown')
            ORDER BY count DESC, kit ASC, phase ASC
            """
        )

        return {
            "summary": {
                "runs": runs,
                "requests": reqs,
                "active_by_phase": active_by_phase,
            }
        }

    def _tool_kit_runs(self, payload: dict[str, Any]) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []

        status = payload.get("status")
        if status:
            clauses.append("status = ?")
            params.append(status)
        kit = payload.get("kit")
        if kit:
            clauses.append("kit = ?")
            params.append(kit)
        phase = payload.get("phase")
        if phase:
            clauses.append("phase = ?")
            params.append(phase)

        limit = payload.get("limit", 50)
        if not isinstance(limit, int) or limit < 1:
            limit = 50
        limit = min(limit, 200)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        params.extend([limit, 0])
        rows = self._db_query(
            f"""
            SELECT
              project_id, run_id, parent_run_id, kit, phase, status,
              started_at, finished_at, exit_code, cwd, project_root,
              orchestration_kit_root, agent_runtime, host, pid,
              capsule_path, manifest_path, log_path, events_path,
              reasoning, experiment_name, verdict
            FROM runs
            {where}
            ORDER BY COALESCE(started_at, '') DESC, run_id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        )

        # Post-process: add duration_seconds, is_stale, is_orphaned
        now = dt.datetime.now(dt.timezone.utc)
        local_hostname = socket.gethostname()
        for row in rows:
            started = row.get("started_at")
            finished = row.get("finished_at")
            duration = None
            if started:
                try:
                    start_dt = dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
                    end_dt = (
                        dt.datetime.fromisoformat(finished.replace("Z", "+00:00"))
                        if finished
                        else now
                    )
                    duration = max(0, int((end_dt - start_dt).total_seconds()))
                except (ValueError, TypeError):
                    pass
            row["duration_seconds"] = duration
            row["is_stale"] = (
                row.get("status") == "running"
                and duration is not None
                and duration > 1800
            )

            is_orphaned = False
            if (
                row.get("status") == "running"
                and row.get("pid")
                and row.get("host") == local_hostname
            ):
                try:
                    os.kill(int(row["pid"]), 0)
                except ProcessLookupError:
                    is_orphaned = True
                except (PermissionError, OSError, TypeError, ValueError):
                    pass
            row["is_orphaned"] = is_orphaned

        return {"runs": rows, "limit": limit, "offset": 0, "has_more": len(rows) == limit}

    def _tool_kit_capsule(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = require_str(payload, "run_id")

        run = self._db_query_one(
            "SELECT capsule_path, manifest_path, log_path, events_path, orchestration_kit_root, project_root FROM runs WHERE run_id = ?",
            (run_id,),
        )
        if run is None:
            raise MCPToolError(f"run not found: {run_id}")

        result: dict[str, Any] = {"run_id": run_id}

        # Read capsule file directly
        capsule_path = run.get("capsule_path")
        if capsule_path:
            ok_root = Path(run.get("orchestration_kit_root", str(self.root)))
            resolved = Path(capsule_path)
            if not resolved.is_absolute():
                resolved = ok_root / resolved
            if resolved.is_file():
                try:
                    text = resolved.read_text(encoding="utf-8", errors="replace")
                    result["capsule"] = {
                        "path": capsule_path,
                        "text": cap_text_bytes(text, self.config.max_output_bytes),
                    }
                except OSError:
                    result["capsule"] = None
            else:
                result["capsule"] = None
        else:
            result["capsule"] = None

        result["artifact_paths"] = {
            "capsule": run.get("capsule_path"),
            "manifest": run.get("manifest_path"),
            "log": run.get("log_path"),
            "events": run.get("events_path"),
        }

        return result

    def _tool_kit_research_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._tool_run({"kit": "research", "action": "status"})

    # --- Process visibility tool handlers ---

    def _tool_kit_active(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return status of all background processes launched by this server."""
        processes: list[dict[str, Any]] = []
        # Read-only snapshot of _background; CPython GIL makes dict iteration safe.
        for run_id, proc in list(self._background.items()):
            rc = proc.poll()
            if rc is None:
                status = "running"
                exit_code = None
            elif rc == 0:
                status = "ok"
                exit_code = 0
            else:
                status = "failed"
                exit_code = rc
            processes.append({
                "run_id": run_id,
                "pid": proc.pid,
                "status": status,
                "exit_code": exit_code,
            })
        return {"processes": processes, "count": len(processes)}

    def _tool_kit_kill(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Terminate a background process by run_id."""
        run_id = require_str(payload, "run_id")
        sig_name = payload.get("signal", "SIGTERM")
        if sig_name not in {"SIGTERM", "SIGKILL"}:
            raise ValueError("signal must be SIGTERM or SIGKILL")

        proc = self._background.get(run_id)
        if proc is None:
            raise MCPToolError(f"run_id not found in active processes: {run_id}")

        sig = signal.SIGTERM if sig_name == "SIGTERM" else signal.SIGKILL

        rc = proc.poll()
        if rc is not None:
            return {
                "run_id": run_id,
                "result": "already_finished",
                "exit_code": rc,
            }

        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            return {
                "run_id": run_id,
                "result": "already_finished",
                "exit_code": proc.poll(),
            }
        except OSError as exc:
            raise MCPToolError(f"failed to send {sig_name} to {run_id}: {exc}") from exc

        return {
            "run_id": run_id,
            "result": "signal_sent",
            "signal": sig_name,
            "pid": proc.pid,
        }

    # --- GC tool handler ---

    def _tool_kit_gc(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Garbage-collect stale runs: re-index, then mark orphans as failed."""
        dry_run = payload.get("dry_run", False)

        # Step 1: Re-index from disk events.jsonl to pick up any run_finished events
        self._reindex()

        # Step 2: Find all "running" runs and check if their PIDs are alive
        local_hostname = socket.gethostname()
        running = self._db_query(
            "SELECT project_id, run_id, pid, host, started_at FROM runs WHERE status = 'running'"
        )

        stale: list[dict[str, Any]] = []
        now = dt.datetime.now(dt.timezone.utc)

        for row in running:
            pid = row.get("pid")
            host = row.get("host")
            reason = None

            # Check if PID is dead (local runs only)
            if pid and host == local_hostname:
                try:
                    os.kill(int(pid), 0)
                except ProcessLookupError:
                    reason = "pid_dead"
                except (PermissionError, OSError, TypeError, ValueError):
                    pass  # process exists or can't check

            # Check if run is ancient (>2 hours with no PID info = likely stale)
            if reason is None and row.get("started_at"):
                try:
                    start_dt = dt.datetime.fromisoformat(
                        row["started_at"].replace("Z", "+00:00")
                    )
                    age_seconds = (now - start_dt).total_seconds()
                    if age_seconds > 7200 and not pid:
                        reason = "no_pid_ancient"
                except (ValueError, TypeError):
                    pass

            if reason:
                stale.append({
                    "project_id": row["project_id"],
                    "run_id": row["run_id"],
                    "pid": pid,
                    "host": host,
                    "reason": reason,
                })

        # Step 3: Mark stale runs as failed
        cleaned = 0
        if not dry_run and stale:
            ts = utc_now()
            conn = self._db_connect()
            try:
                for entry in stale:
                    conn.execute(
                        "UPDATE runs SET status = 'failed', exit_code = 137, finished_at = ? WHERE project_id = ? AND run_id = ?",
                        (ts, entry["project_id"], entry["run_id"]),
                    )
                    cleaned += 1
                conn.commit()
            finally:
                conn.close()

        return {
            "stale_runs": stale,
            "cleaned": cleaned,
            "dry_run": dry_run,
            "still_running": len(running) - len(stale),
        }

    # --- Legacy orchestrator.* tool handlers ---

    def _tool_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        kit = require_str(payload, "kit")
        action = require_str(payload, "action")
        args = [str(item) for item in optional_list(payload, "args")]
        env_overrides = coerce_env(payload.get("env"))

        # Options must come BEFORE positional args due to argparse.REMAINDER
        # in tools/kit (see _launch_background comment for details).
        cmd = [str(self._tool_path("kit")), "--json"]
        reasoning = payload.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            cmd.extend(["--reasoning", reasoning])
        cmd.extend([kit, action, *args])
        proc = self._run_cmd(cmd, extra_env=env_overrides)

        try:
            parsed = parse_json_tail(proc.stdout)
        except ValueError as exc:
            tail = cap_text_bytes(proc.stdout + "\n" + proc.stderr, self.config.max_output_bytes)
            raise MCPToolError(f"orchestrator.run failed to parse output: {exc}; tail={tail}") from exc

        paths = parsed.get("paths", {}) if isinstance(parsed.get("paths"), dict) else {}
        result = {
            "run_id": parsed.get("run_id"),
            "capsule_path": paths.get("capsule"),
            "manifest_path": paths.get("manifest"),
            "events_path": paths.get("events"),
            "log_paths": [paths.get("log")] if isinstance(paths.get("log"), str) else [],
        }

        if proc.returncode != 0 and parsed.get("status") != "failed":
            # Preserve pointer-only shape even on non-zero return.
            result["status"] = "failed"
        return result

    def _tool_request_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        from_kit = require_str(payload, "from_kit")
        from_phase = payload.get("from_phase")
        if from_phase is not None and (not isinstance(from_phase, str) or not from_phase.strip()):
            raise ValueError("from_phase must be a non-empty string when provided")
        to_kit = require_str(payload, "to_kit")
        action = require_str(payload, "action")

        args = [str(item) for item in optional_list(payload, "args")]
        must_read = [str(item) for item in optional_list(payload, "must_read")]
        deliverables = [str(item) for item in optional_list(payload, "deliverables_expected")]

        read_budget_raw = payload.get("read_budget", {})
        if read_budget_raw is None:
            read_budget_raw = {}
        if not isinstance(read_budget_raw, dict):
            raise ValueError("read_budget must be an object")

        allowed_paths_raw = read_budget_raw.get("allowed_paths", [])
        if allowed_paths_raw is None:
            allowed_paths_raw = []
        if not isinstance(allowed_paths_raw, list):
            raise ValueError("read_budget.allowed_paths must be a list")
        allowed_paths = [str(item) for item in allowed_paths_raw]

        priority = payload.get("priority", "normal")
        if not isinstance(priority, str):
            raise ValueError("priority must be a string")

        max_files = int(read_budget_raw.get("max_files", 8))
        max_total_bytes = int(read_budget_raw.get("max_total_bytes", 300000))

        run_id_raw = payload.get("run_id")
        if run_id_raw is None:
            run_id = f"orphan-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        elif isinstance(run_id_raw, str) and run_id_raw.strip():
            run_id = run_id_raw
        else:
            raise ValueError("run_id must be a non-empty string when provided")

        cmd = [
            str(self._tool_path("kit")),
            "request",
            "--json",
            "--from",
            from_kit,
            "--to",
            to_kit,
            "--action",
            action,
            "--run-id",
            run_id,
            "--max-files",
            str(max(max_files, 1)),
            "--max-total-bytes",
            str(max(max_total_bytes, 1)),
            "--priority",
            priority,
        ]
        if isinstance(from_phase, str) and from_phase.strip():
            cmd.extend(["--from-phase", from_phase])

        reasoning = payload.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            cmd.extend(["--reasoning", reasoning])

        for item in args:
            cmd.extend(["--arg", item])
        for item in must_read:
            cmd.extend(["--must-read", item])
        for item in allowed_paths:
            cmd.extend(["--allowed-path", item])
        for item in deliverables:
            cmd.extend(["--deliverable", item])

        proc = self._run_cmd(cmd)

        try:
            parsed = parse_json_tail(proc.stdout)
        except ValueError as exc:
            tail = cap_text_bytes(proc.stdout + "\n" + proc.stderr, self.config.max_output_bytes)
            raise MCPToolError(f"orchestrator.request_create failed to parse output: {exc}; tail={tail}") from exc

        request_id = parsed.get("request_id")
        request_path = parsed.get("path")
        if not isinstance(request_id, str) or not isinstance(request_path, str):
            raise MCPToolError("orchestrator.request_create produced invalid pointer output")

        return {
            "request_id": request_id,
            "request_path": request_path,
        }

    def _tool_pump(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = payload.get("mode")
        if mode not in {"once", "queue"}:
            raise ValueError("mode must be one of: once, queue")

        request_id = payload.get("request_id")
        cmd = [str(self._tool_path("pump")), "--once", "--json"]
        if mode == "once":
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("request_id is required when mode=once")
            cmd.extend(["--request", request_id])

        proc = self._run_cmd(cmd)

        try:
            parsed = parse_json_tail(proc.stdout)
        except ValueError:
            message = cap_text_bytes(proc.stderr or proc.stdout, self.config.max_output_bytes)
            raise MCPToolError(f"orchestrator.pump failed: {message}") from None

        return {
            "response_path": parsed.get("response_path"),
            "status": parsed.get("status"),
            "child_run_id": parsed.get("child_run_id"),
            "capsule_path": parsed.get("capsule_path"),
            "manifest_path": parsed.get("manifest_path"),
        }

    def _latest_run_id(self) -> str:
        runs_dir = self.root / "runs"
        if not runs_dir.is_dir():
            raise MCPToolError("runs directory does not exist")

        candidates: list[Path] = []
        for child in runs_dir.iterdir():
            if not child.is_dir():
                continue
            if (child / "events.jsonl").is_file():
                candidates.append(child)

        if not candidates:
            raise MCPToolError("no runs available")

        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        return latest.name

    def _tool_run_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = require_str(payload, "run_id")
        if run_id == "latest":
            run_id = self._latest_run_id()

        run_root = self.root / "runs" / run_id
        if not run_root.is_dir():
            raise MCPToolError(f"run not found: {run_id}")

        events_path = run_root / "events.jsonl"
        capsules = sorted(rel_to(self.root, p) for p in (run_root / "capsules").glob("*") if p.is_file())
        manifests = sorted(rel_to(self.root, p) for p in (run_root / "manifests").glob("*") if p.is_file())
        logs = sorted(rel_to(self.root, p) for p in (run_root / "logs").glob("*") if p.is_file())

        return {
            "run_id": run_id,
            "events_path": rel_to(self.root, events_path),
            "capsules": capsules,
            "manifests": manifests,
            "logs": logs,
        }

    def _safe_log_path(self, raw: str) -> Path:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError("path must resolve inside ORCHESTRATION_KIT_ROOT") from exc
        return resolved

    def _tool_query_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_path = require_str(payload, "path")
        mode = payload.get("mode")
        if mode not in {"tail", "grep", "lean_summarize"}:
            raise ValueError("mode must be one of: tail, grep, lean_summarize")

        target = self._safe_log_path(raw_path)
        rel_path = rel_to(self.root, target)

        query_mode = "lean-summary" if mode == "lean_summarize" else mode
        cmd = [str(self._tool_path("query-log")), query_mode]

        if mode == "tail":
            n = int(payload.get("n", 120))
            cmd.extend([str(target), str(max(n, 1))])
        elif mode == "grep":
            pattern = payload.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise ValueError("pattern is required for grep mode")
            cmd.extend([pattern, str(target)])
        else:
            cmd.append(str(target))

        proc = self._run_cmd(cmd)
        snippet_source = proc.stdout if proc.stdout.strip() else proc.stderr
        snippet = cap_text_bytes(snippet_source, self.config.max_output_bytes)

        return {
            "snippet": snippet,
            "path": rel_path,
            "hint": "Use grep/tail modes; full log remains on disk.",
        }

    # --- Dispatch ---

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")

        # Execution tools — fire-and-forget, no lock needed
        if name == "kit.tdd":
            return self._tool_kit_tdd(arguments)
        if name == "kit.research_cycle":
            return self._tool_kit_research_cycle(arguments)
        if name == "kit.research_full":
            return self._tool_kit_research_full(arguments)
        if name == "kit.research_program":
            return self._tool_kit_research_program(arguments)
        if name == "kit.math":
            return self._tool_kit_math(arguments)

        # Process visibility tools — no lock needed (read-only or pid-safe)
        if name == "kit.active":
            return self._tool_kit_active(arguments)
        if name == "kit.kill":
            return self._tool_kit_kill(arguments)
        if name == "kit.gc":
            return self._tool_kit_gc(arguments)

        with self._lock:
            # Legacy orchestrator.* tools
            if name == "orchestrator.run":
                return self._tool_run(arguments)
            if name == "orchestrator.request_create":
                return self._tool_request_create(arguments)
            if name == "orchestrator.pump":
                return self._tool_pump(arguments)
            if name == "orchestrator.run_info":
                return self._tool_run_info(arguments)
            if name == "orchestrator.query_log":
                return self._tool_query_log(arguments)
            # Dashboard query tools
            if name == "kit.status":
                return self._tool_kit_status(arguments)
            if name == "kit.runs":
                return self._tool_kit_runs(arguments)
            if name == "kit.capsule":
                return self._tool_kit_capsule(arguments)
            if name == "kit.research_status":
                return self._tool_kit_research_status(arguments)

        raise ValueError(f"unknown tool: {name}")


class MCPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], config: ServerConfig):
        self.config = config
        self.facade = MasterKitFacade(config)
        super().__init__(server_address, MCPHandler)


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "orchestration-kit-mcp/0.2"

    @property
    def typed_server(self) -> MCPServer:
        assert isinstance(self.server, MCPServer)
        return self.server

    def log_message(self, fmt: str, *args: Any) -> None:
        stamp = utc_now()
        message = f"[{stamp}] {self.client_address[0]} {fmt % args}"
        print(message, flush=True)

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.typed_server.config.token}"
        return auth == expected

    def _jsonrpc_result(self, request_id: Any, result: Any) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

    def _jsonrpc_error(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/mcp":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        if not self._is_authorized():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        try:
            raw_len = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid content-length"})
            return

        raw_body = self.rfile.read(raw_len)
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return

        if not isinstance(body, dict):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "json body must be an object"})
            return

        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params", {})

        if not isinstance(method, str):
            payload = self._jsonrpc_error(request_id, -32600, "invalid request: method is required")
            self._write_json(HTTPStatus.OK, payload)
            return

        if params is None:
            params = {}
        if not isinstance(params, dict):
            payload = self._jsonrpc_error(request_id, -32602, "invalid params: must be an object")
            self._write_json(HTTPStatus.OK, payload)
            return

        try:
            response_payload = self._dispatch_jsonrpc(method, params, request_id)
        except MCPToolError as exc:
            payload = self._jsonrpc_result(
                request_id,
                {
                    "isError": True,
                    "content": [{"type": "text", "text": cap_text_bytes(str(exc), self.typed_server.config.max_output_bytes)}],
                },
            )
            self._write_json(HTTPStatus.OK, payload)
            return
        except ValueError as exc:
            payload = self._jsonrpc_error(request_id, -32602, str(exc))
            self._write_json(HTTPStatus.OK, payload)
            return
        except Exception as exc:  # pragma: no cover - defensive fallback
            payload = self._jsonrpc_error(request_id, -32000, f"internal error: {exc}")
            self._write_json(HTTPStatus.OK, payload)
            return

        self._write_json(HTTPStatus.OK, response_payload)

    def _dispatch_jsonrpc(self, method: str, params: dict[str, Any], request_id: Any) -> dict[str, Any]:
        if method == "initialize":
            return self._jsonrpc_result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": "orchestration-kit-mcp",
                        "version": "0.2.0",
                    },
                    "capabilities": {"tools": {}},
                },
            )

        if method == "notifications/initialized":
            return self._jsonrpc_result(request_id, {})

        if method == "tools/list":
            return self._jsonrpc_result(request_id, {"tools": TOOL_DEFINITIONS})

        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("tools/call requires name")
            arguments = params.get("arguments", {})
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                raise ValueError("tools/call arguments must be an object")

            result = self.typed_server.facade.call_tool(name, arguments)
            text = cap_text_bytes(
                json.dumps(result, sort_keys=True),
                self.typed_server.config.max_output_bytes,
            )
            return self._jsonrpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": result,
                },
            )

        if method == "ping":
            return self._jsonrpc_result(request_id, {"ok": True, "ts": utc_now()})

        return self._jsonrpc_error(request_id, -32601, f"method not found: {method}")


# --- stdio transport ---


def _stdio_result(request_id: Any, data: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": data}


def _stdio_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _dispatch_stdio(
    facade: MasterKitFacade,
    config: ServerConfig,
    method: str,
    params: dict[str, Any],
    request_id: Any,
) -> dict[str, Any]:
    """Dispatch a JSON-RPC request for stdio transport."""
    if method == "initialize":
        return _stdio_result(request_id, {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "orchestration-kit-mcp", "version": "0.2.0"},
            "capabilities": {"tools": {}},
        })

    if method == "notifications/initialized":
        return _stdio_result(request_id, {})

    if method == "tools/list":
        return _stdio_result(request_id, {"tools": TOOL_DEFINITIONS})

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _stdio_error(request_id, -32602, "tools/call requires name")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _stdio_error(request_id, -32602, "tools/call arguments must be an object")

        try:
            result = facade.call_tool(name, arguments)
            text = cap_text_bytes(json.dumps(result, sort_keys=True), config.max_output_bytes)
            return _stdio_result(request_id, {
                "content": [{"type": "text", "text": text}],
                "structuredContent": result,
            })
        except MCPToolError as exc:
            return _stdio_result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text": cap_text_bytes(str(exc), config.max_output_bytes)}],
            })
        except ValueError as exc:
            return _stdio_error(request_id, -32602, str(exc))
        except Exception as exc:
            return _stdio_error(request_id, -32000, f"internal error: {exc}")

    if method == "ping":
        return _stdio_result(request_id, {"ok": True, "ts": utc_now()})

    return _stdio_error(request_id, -32601, f"method not found: {method}")


def run_stdio(facade: MasterKitFacade, config: ServerConfig) -> int:
    """Run MCP server over stdio (newline-delimited JSON-RPC on stdin/stdout)."""
    print(
        f"orchestration-kit mcp stdio ready root={config.root}",
        file=sys.stderr,
        flush=True,
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            body = json.loads(line)
        except json.JSONDecodeError:
            response = _stdio_error(None, -32700, "parse error")
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            continue

        if not isinstance(body, dict):
            response = _stdio_error(None, -32600, "invalid request")
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            continue

        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params", {})
        if params is None:
            params = {}

        if not isinstance(method, str):
            response = _stdio_error(request_id, -32600, "method is required")
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            continue

        response = _dispatch_stdio(facade, config, method, params, request_id)
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    return 0


# --- Config and main ---


def load_config(argv: list[str]) -> ServerConfig:
    parser = argparse.ArgumentParser(prog="mcp/server.py")
    parser.add_argument("--root", default=os.getenv("ORCHESTRATION_KIT_ROOT"))
    parser.add_argument("--host", default=os.getenv("ORCHESTRATION_KIT_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=env_int("ORCHESTRATION_KIT_MCP_PORT", 7337))
    parser.add_argument("--token", default=os.getenv("ORCHESTRATION_KIT_MCP_TOKEN"))
    parser.add_argument("--max-output-bytes", type=int, default=env_int("ORCHESTRATION_KIT_MCP_MAX_OUTPUT_BYTES", 32000))
    parser.add_argument("--log-dir", default=os.getenv("ORCHESTRATION_KIT_MCP_LOG_DIR", "runs/mcp-logs"))
    parser.add_argument(
        "--transport",
        default=os.getenv("ORCHESTRATION_KIT_MCP_TRANSPORT", "http"),
        choices=["http", "stdio"],
    )

    args = parser.parse_args(argv)

    if not args.root:
        raise SystemExit("ORCHESTRATION_KIT_ROOT is required (or --root)")
    if args.transport == "http" and not args.token:
        raise SystemExit("ORCHESTRATION_KIT_MCP_TOKEN is required for http transport (or --token)")

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"ORCHESTRATION_KIT_ROOT does not exist: {root}")

    log_dir = Path(args.log_dir)
    if not log_dir.is_absolute():
        log_dir = root / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    max_output_bytes = max(int(args.max_output_bytes), 1)

    dashboard_port = env_int("ORCHESTRATION_KIT_DASHBOARD_PORT", 7340)
    dashboard_url = f"http://127.0.0.1:{dashboard_port}"

    project_root_raw = os.getenv("PROJECT_ROOT")
    project_root = Path(project_root_raw).resolve() if project_root_raw else None

    # KIT_STATE_DIR: greenfield projects set this to ".kit", monorepo uses ".".
    # Try env first, then detect from project structure.
    kit_state_dir = os.getenv("KIT_STATE_DIR")
    if not kit_state_dir and project_root and (project_root / ".kit").is_dir():
        kit_state_dir = ".kit"

    return ServerConfig(
        root=root,
        host=str(args.host),
        port=int(args.port),
        token=str(args.token or ""),
        max_output_bytes=max_output_bytes,
        log_dir=log_dir,
        transport=str(args.transport),
        dashboard_url=dashboard_url,
        project_root=project_root,
        kit_state_dir=kit_state_dir,
    )


def main(argv: list[str]) -> int:
    config = load_config(argv)

    if config.transport == "stdio":
        facade = MasterKitFacade(config)
        return run_stdio(facade, config)

    server = MCPServer((config.host, config.port), config)

    print(
        f"orchestration-kit mcp ready url=http://{config.host}:{config.port}/mcp "
        f"root={config.root} max_output_bytes={config.max_output_bytes}",
        flush=True,
    )

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
