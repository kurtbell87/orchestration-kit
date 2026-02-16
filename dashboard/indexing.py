"""Index projects: scan runs directories and populate SQLite."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .config import db_path, current_orchestration_kit_root, current_project_root
from .schema import ensure_schema
from .registry import load_registry, upsert_registry_project
from .parsing import parse_run


def _insert_project(conn: sqlite3.Connection, project: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO projects(project_id, label, orchestration_kit_root, project_root, registered_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
          label = excluded.label,
          orchestration_kit_root = excluded.orchestration_kit_root,
          project_root = excluded.project_root,
          registered_at = excluded.registered_at,
          updated_at = excluded.updated_at
        """,
        (
            project["project_id"],
            project["label"],
            project["orchestration_kit_root"],
            project["project_root"],
            project.get("registered_at"),
            project.get("updated_at"),
        ),
    )


def _delete_project_rows(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute("DELETE FROM runs WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM requests WHERE project_id = ?", (project_id,))


def _insert_run(conn: sqlite3.Connection, run: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO runs(
          project_id, run_id, parent_run_id, kit, phase, started_at, finished_at,
          exit_code, status, capsule_path, manifest_path, log_path, events_path,
          cwd, project_root, orchestration_kit_root, agent_runtime, host, pid, reasoning
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run["project_id"],
            run["run_id"],
            run["parent_run_id"],
            run["kit"],
            run["phase"],
            run["started_at"],
            run["finished_at"],
            run["exit_code"],
            run["status"],
            run["capsule_path"],
            run["manifest_path"],
            run["log_path"],
            run["events_path"],
            run["cwd"],
            run["project_root"],
            run["orchestration_kit_root"],
            run["agent_runtime"],
            run["host"],
            run["pid"],
            run.get("reasoning"),
        ),
    )


def _insert_request(conn: sqlite3.Connection, request: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO requests(
          project_id, request_id, parent_run_id, child_run_id,
          from_kit, from_phase, to_kit, to_phase, action,
          status, request_path, response_path, enqueued_ts, completed_ts, reasoning
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, request_id) DO UPDATE SET
          parent_run_id = excluded.parent_run_id,
          child_run_id = excluded.child_run_id,
          from_kit = excluded.from_kit,
          from_phase = excluded.from_phase,
          to_kit = excluded.to_kit,
          to_phase = excluded.to_phase,
          action = excluded.action,
          status = excluded.status,
          request_path = excluded.request_path,
          response_path = excluded.response_path,
          enqueued_ts = excluded.enqueued_ts,
          completed_ts = excluded.completed_ts,
          reasoning = excluded.reasoning
        """,
        (
            request["project_id"],
            request["request_id"],
            request["parent_run_id"],
            request["child_run_id"],
            request["from_kit"],
            request["from_phase"],
            request["to_kit"],
            request["to_phase"],
            request["action"],
            request["status"],
            request["request_path"],
            request["response_path"],
            request["enqueued_ts"],
            request["completed_ts"],
            request.get("reasoning"),
        ),
    )


def index_projects(
    projects: list[dict[str, Any]],
    *,
    cleanup_stale_projects: bool = True,
) -> dict[str, Any]:
    db = db_path()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    if cleanup_stale_projects:
        active_ids = {p["project_id"] for p in projects}
        stale_rows = conn.execute("SELECT project_id FROM projects").fetchall()
        for row in stale_rows:
            pid = row["project_id"]
            if pid not in active_ids:
                _delete_project_rows(conn, pid)
                conn.execute("DELETE FROM projects WHERE project_id = ?", (pid,))

    indexed_runs = 0
    indexed_requests = 0
    missing_roots: list[str] = []

    for project in projects:
        _insert_project(conn, project)
        _delete_project_rows(conn, project["project_id"])

        runs_dir = project["orchestration_kit_root_path"] / "runs"
        if not runs_dir.is_dir():
            missing_roots.append(project["orchestration_kit_root"])
            continue

        run_roots = sorted(
            p for p in runs_dir.iterdir() if p.is_dir() and (p / "events.jsonl").is_file()
        )
        for run_root in run_roots:
            run, requests = parse_run(project=project, run_root=run_root)
            _insert_run(conn, run)
            indexed_runs += 1
            for request in requests:
                _insert_request(conn, request)
                indexed_requests += 1

    conn.commit()
    conn.close()

    return {
        "projects_indexed": len(projects),
        "runs_indexed": indexed_runs,
        "requests_indexed": indexed_requests,
        "missing_roots": missing_roots,
        "db_path": str(db),
    }


def prepare_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for project in projects:
        mk = Path(project["orchestration_kit_root"]).expanduser().resolve()
        pr = Path(project["project_root"]).expanduser().resolve()
        prepared.append(
            {
                **project,
                "orchestration_kit_root": str(mk),
                "project_root": str(pr),
                "orchestration_kit_root_path": mk,
                "project_root_path": pr,
            }
        )
    return prepared


def maybe_seed_registry() -> list[dict[str, Any]]:
    projects = load_registry()
    if projects:
        return projects

    mk = current_orchestration_kit_root()
    pr = current_project_root(mk)
    upsert_registry_project(orchestration_kit_root=mk, project_root=pr, label=pr.name)
    return load_registry()
