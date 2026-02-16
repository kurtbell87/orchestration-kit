"""HTTP request handler and server for the dashboard."""
from __future__ import annotations

import json
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import now_iso
from .payloads import (
    load_dashboard_rows,
    summary_payload,
    graph_payload,
    list_runs_payload,
    run_detail_payload,
    artifact_payload,
    project_docs_payload,
    capsule_preview_payload,
)
from .dag import dag_payload
from .indexing import maybe_seed_registry, prepare_projects, index_projects

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_template_cache: dict[str, tuple[float, str]] = {}


def _load_template(name: str) -> str:
    path = TEMPLATE_DIR / name
    mtime = path.stat().st_mtime
    cached = _template_cache.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    content = path.read_text(encoding="utf-8")
    _template_cache[name] = (mtime, content)
    return content


class DashboardHandler(BaseHTTPRequestHandler):
    state_lock = threading.Lock()
    projects: list[dict[str, Any]] = []

    @property
    def json_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def log_message(self, fmt: str, *args: Any) -> None:
        stamp = now_iso()
        print(f"[{stamp}] dashboard {self.client_address[0]} {fmt % args}", flush=True)

    def _write(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._write(status, body, "application/json")

    def _read_json_body(self) -> dict[str, Any]:
        raw_len = int(self.headers.get("Content-Length", "0") or "0")
        if raw_len <= 0:
            return {}
        data = self.rfile.read(raw_len)
        try:
            body = json.loads(data.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"invalid json body: {exc}") from exc
        if not isinstance(body, dict):
            raise ValueError("json body must be an object")
        return body

    def _query(self) -> dict[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        raw = urllib.parse.parse_qs(parsed.query)
        return {k: v[-1] for k, v in raw.items() if v}

    def _route_path(self) -> str:
        return urllib.parse.urlparse(self.path).path

    def do_GET(self) -> None:  # noqa: N802
        route = self._route_path()
        try:
            if route == "/":
                html = _load_template("index.html")
                self._write(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")
                return

            if route == "/api/projects":
                rows = load_dashboard_rows(
                    "SELECT project_id, label, orchestration_kit_root, project_root FROM projects ORDER BY label ASC"
                )
                self._write_json(HTTPStatus.OK, {"projects": rows})
                return

            if route == "/api/summary":
                query = self._query()
                payload = summary_payload(query.get("project_id"))
                self._write_json(HTTPStatus.OK, {"summary": payload})
                return

            if route == "/api/graph":
                query = self._query()
                payload = graph_payload(query.get("project_id"))
                self._write_json(HTTPStatus.OK, payload)
                return

            if route == "/api/dag":
                query = self._query()
                payload = dag_payload(query.get("project_id"))
                self._write_json(HTTPStatus.OK, payload)
                return

            if route == "/api/active":
                query = self._query()
                params: list[Any] = []
                where = "WHERE status = 'running'"
                project_id = query.get("project_id")
                if project_id:
                    where += " AND project_id = ?"
                    params.append(project_id)
                runs = load_dashboard_rows(
                    f"""
                    SELECT project_id, run_id, kit, phase, status, started_at, agent_runtime, host, pid, project_root
                    FROM runs
                    {where}
                    ORDER BY COALESCE(started_at, '') DESC
                    """,
                    tuple(params),
                )
                self._write_json(HTTPStatus.OK, {"runs": runs})
                return

            if route == "/api/runs":
                payload = list_runs_payload(self._query())
                self._write_json(HTTPStatus.OK, payload)
                return

            if route == "/api/run":
                query = self._query()
                project_id = query.get("project_id")
                run_id = query.get("run_id")
                if not project_id or not run_id:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": "project_id and run_id are required"})
                    return
                payload = run_detail_payload(project_id, run_id)
                self._write_json(HTTPStatus.OK, payload)
                return

            if route == "/api/artifact":
                query = self._query()
                project_id = query.get("project_id")
                raw_path = query.get("path")
                if not project_id or not raw_path:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": "project_id and path are required"})
                    return
                max_bytes_raw = query.get("max_bytes")
                max_bytes: int | None = None
                if isinstance(max_bytes_raw, str) and max_bytes_raw.strip():
                    try:
                        max_bytes = int(max_bytes_raw)
                    except ValueError:
                        self._write_json(HTTPStatus.BAD_REQUEST, {"error": "max_bytes must be an integer"})
                        return
                scope = query.get("scope") or "auto"
                payload = artifact_payload(project_id=project_id, raw_path=raw_path, max_bytes=max_bytes, scope=scope)
                self._write_json(HTTPStatus.OK, payload)
                return

            if route == "/api/project-docs":
                query = self._query()
                project_id = query.get("project_id")
                if not project_id:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": "project_id is required"})
                    return
                payload = project_docs_payload(project_id)
                self._write_json(HTTPStatus.OK, payload)
                return

            if route == "/api/capsule-preview":
                query = self._query()
                project_id = query.get("project_id")
                run_id = query.get("run_id")
                if not project_id or not run_id:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": "project_id and run_id are required"})
                    return
                payload = capsule_preview_payload(project_id, run_id)
                self._write_json(HTTPStatus.OK, payload)
                return

            if route == "/health":
                self._write_json(HTTPStatus.OK, {"ok": True, "ts": now_iso()})
                return

            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except FileNotFoundError as exc:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except KeyError as exc:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        route = self._route_path()
        if route != "/api/refresh":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        try:
            body = self._read_json_body()
            project_id = body.get("project_id")
            if project_id is not None and (not isinstance(project_id, str) or not project_id):
                raise ValueError("project_id must be a non-empty string when provided")

            with self.state_lock:
                projects = maybe_seed_registry()
                prepared = prepare_projects(projects)
                if isinstance(project_id, str):
                    prepared = [p for p in prepared if p["project_id"] == project_id]
                result = index_projects(
                    prepared,
                    cleanup_stale_projects=not isinstance(project_id, str),
                )

            self._write_json(HTTPStatus.OK, {"refreshed": result})
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
