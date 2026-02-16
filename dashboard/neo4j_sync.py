"""SQLite-to-Neo4j sync for dashboard run/request data.

Requires the ``neo4j`` Python package (``pip install neo4j``).
All writes use MERGE so the operation is idempotent.
"""
from __future__ import annotations

import sqlite3
from typing import Any


def _require_neo4j() -> Any:
    """Import and return the neo4j module, raising a clear error if missing."""
    try:
        import neo4j  # noqa: F811
        return neo4j
    except ImportError:
        raise ImportError(
            "The 'neo4j' package is required for Neo4j sync. "
            "Install it with: pip install neo4j"
        )


def _merge_projects(tx: Any, rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        tx.run(
            """
            MERGE (p:Project {project_id: $project_id})
            SET p.label = $label,
                p.project_root = $project_root,
                p.orchestration_kit_root = $orchestration_kit_root
            """,
            project_id=row["project_id"],
            label=row.get("label") or "",
            project_root=row.get("project_root") or "",
            orchestration_kit_root=row.get("orchestration_kit_root") or "",
        )
        count += 1
    return count


def _merge_runs(tx: Any, rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        tx.run(
            """
            MERGE (r:Run {run_id: $run_id})
            SET r.kit = $kit,
                r.phase = $phase,
                r.status = $status,
                r.exit_code = $exit_code,
                r.started_at = $started_at,
                r.finished_at = $finished_at,
                r.reasoning = $reasoning,
                r.project_id = $project_id
            """,
            run_id=row["run_id"],
            kit=row.get("kit"),
            phase=row.get("phase"),
            status=row.get("status"),
            exit_code=row.get("exit_code"),
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            reasoning=row.get("reasoning"),
            project_id=row["project_id"],
        )

        # BELONGS_TO edge
        tx.run(
            """
            MATCH (r:Run {run_id: $run_id})
            MATCH (p:Project {project_id: $project_id})
            MERGE (r)-[:BELONGS_TO]->(p)
            """,
            run_id=row["run_id"],
            project_id=row["project_id"],
        )

        # CHILD_OF edge (parent-child DAG)
        parent = row.get("parent_run_id")
        if isinstance(parent, str) and parent:
            tx.run(
                """
                MATCH (child:Run {run_id: $child_id})
                MERGE (parent:Run {run_id: $parent_id})
                MERGE (child)-[:CHILD_OF]->(parent)
                """,
                child_id=row["run_id"],
                parent_id=parent,
            )

        count += 1
    return count


def _merge_interop_edges(tx: Any, rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        parent = row.get("parent_run_id")
        child = row.get("child_run_id")
        if not isinstance(parent, str) or not parent:
            continue
        if not isinstance(child, str) or not child:
            continue

        tx.run(
            """
            MATCH (a:Run {run_id: $parent_id})
            MATCH (b:Run {run_id: $child_id})
            MERGE (a)-[r:INTEROP {request_id: $request_id}]->(b)
            SET r.action = $action,
                r.from_kit = $from_kit,
                r.from_phase = $from_phase,
                r.reasoning = $reasoning
            """,
            parent_id=parent,
            child_id=child,
            request_id=row.get("request_id") or "",
            action=row.get("action"),
            from_kit=row.get("from_kit"),
            from_phase=row.get("from_phase"),
            reasoning=row.get("reasoning"),
        )
        count += 1
    return count


def sync_project(project_id: str, neo4j_driver: Any, db_path: str) -> dict[str, int]:
    """Sync one project from SQLite to Neo4j. Returns counts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    projects = [dict(r) for r in conn.execute(
        "SELECT * FROM projects WHERE project_id = ?", (project_id,)
    ).fetchall()]
    runs = [dict(r) for r in conn.execute(
        "SELECT * FROM runs WHERE project_id = ?", (project_id,)
    ).fetchall()]
    requests = [dict(r) for r in conn.execute(
        "SELECT * FROM requests WHERE project_id = ?", (project_id,)
    ).fetchall()]
    conn.close()

    with neo4j_driver.session() as session:
        p_count = session.execute_write(lambda tx: _merge_projects(tx, projects))
        r_count = session.execute_write(lambda tx: _merge_runs(tx, runs))
        e_count = session.execute_write(lambda tx: _merge_interop_edges(tx, requests))

    return {
        "projects_synced": p_count,
        "runs_synced": r_count,
        "interop_edges_synced": e_count,
    }


def sync_all(neo4j_driver: Any, db_path: str) -> dict[str, int]:
    """Sync all projects from SQLite to Neo4j. Returns aggregate counts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    projects = [dict(r) for r in conn.execute("SELECT * FROM projects").fetchall()]
    runs = [dict(r) for r in conn.execute("SELECT * FROM runs").fetchall()]
    requests = [dict(r) for r in conn.execute("SELECT * FROM requests").fetchall()]
    conn.close()

    with neo4j_driver.session() as session:
        p_count = session.execute_write(lambda tx: _merge_projects(tx, projects))
        r_count = session.execute_write(lambda tx: _merge_runs(tx, runs))
        e_count = session.execute_write(lambda tx: _merge_interop_edges(tx, requests))

    return {
        "projects_synced": p_count,
        "runs_synced": r_count,
        "interop_edges_synced": e_count,
    }
