#!/usr/bin/env python3
"""HTTP MCP facade for master-kit tools."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import threading
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


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "master.run",
        "description": "Run a master-kit action via tools/kit and return pointer-only paths.",
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
        "name": "master.request_create",
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
        "name": "master.pump",
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
        "name": "master.run_info",
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
        "name": "master.query_log",
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
]


class MasterKitFacade:
    def __init__(self, config: ServerConfig):
        self.config = config
        self.root = config.root
        self._lock = threading.Lock()

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
        env["MASTER_KIT_ROOT"] = str(self.root)
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

    def _tool_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        kit = require_str(payload, "kit")
        action = require_str(payload, "action")
        args = [str(item) for item in optional_list(payload, "args")]
        env_overrides = coerce_env(payload.get("env"))

        cmd = [str(self._tool_path("kit")), "--json", kit, action, *args]
        reasoning = payload.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            cmd.extend(["--reasoning", reasoning])
        proc = self._run_cmd(cmd, extra_env=env_overrides)

        try:
            parsed = parse_json_tail(proc.stdout)
        except ValueError as exc:
            tail = cap_text_bytes(proc.stdout + "\n" + proc.stderr, self.config.max_output_bytes)
            raise MCPToolError(f"master.run failed to parse output: {exc}; tail={tail}") from exc

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
            raise MCPToolError(f"master.request_create failed to parse output: {exc}; tail={tail}") from exc

        request_id = parsed.get("request_id")
        request_path = parsed.get("path")
        if not isinstance(request_id, str) or not isinstance(request_path, str):
            raise MCPToolError("master.request_create produced invalid pointer output")

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
            raise MCPToolError(f"master.pump failed: {message}") from None

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
            raise ValueError("path must resolve inside MASTER_KIT_ROOT") from exc
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

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")

        with self._lock:
            if name == "master.run":
                return self._tool_run(arguments)
            if name == "master.request_create":
                return self._tool_request_create(arguments)
            if name == "master.pump":
                return self._tool_pump(arguments)
            if name == "master.run_info":
                return self._tool_run_info(arguments)
            if name == "master.query_log":
                return self._tool_query_log(arguments)

        raise ValueError(f"unknown tool: {name}")


class MCPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], config: ServerConfig):
        self.config = config
        self.facade = MasterKitFacade(config)
        super().__init__(server_address, MCPHandler)


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "master-kit-mcp/0.1"

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
                        "name": "master-kit-mcp",
                        "version": "0.1.0",
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


def load_config(argv: list[str]) -> ServerConfig:
    parser = argparse.ArgumentParser(prog="mcp/server.py")
    parser.add_argument("--root", default=os.getenv("MASTER_KIT_ROOT"))
    parser.add_argument("--host", default=os.getenv("MASTER_KIT_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=env_int("MASTER_KIT_MCP_PORT", 7337))
    parser.add_argument("--token", default=os.getenv("MASTER_KIT_MCP_TOKEN"))
    parser.add_argument("--max-output-bytes", type=int, default=env_int("MASTER_KIT_MCP_MAX_OUTPUT_BYTES", 32000))
    parser.add_argument("--log-dir", default=os.getenv("MASTER_KIT_MCP_LOG_DIR", "runs/mcp-logs"))

    args = parser.parse_args(argv)

    if not args.root:
        raise SystemExit("MASTER_KIT_ROOT is required (or --root)")
    if not args.token:
        raise SystemExit("MASTER_KIT_MCP_TOKEN is required (or --token)")

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"MASTER_KIT_ROOT does not exist: {root}")

    log_dir = Path(args.log_dir)
    if not log_dir.is_absolute():
        log_dir = root / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    max_output_bytes = max(int(args.max_output_bytes), 1)

    return ServerConfig(
        root=root,
        host=str(args.host),
        port=int(args.port),
        token=str(args.token),
        max_output_bytes=max_output_bytes,
        log_dir=log_dir,
    )


def main(argv: list[str]) -> int:
    config = load_config(argv)
    server = MCPServer((config.host, config.port), config)

    print(
        f"master-kit mcp ready url=http://{config.host}:{config.port}/mcp "
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
    raise SystemExit(main(os.sys.argv[1:]))
