"""SQLite DDL for the dashboard database."""
from __future__ import annotations

import sqlite3


def _migrate_reasoning_columns(conn: sqlite3.Connection) -> None:
    for stmt in [
        "ALTER TABLE runs ADD COLUMN reasoning TEXT",
        "ALTER TABLE requests ADD COLUMN reasoning TEXT",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def _migrate_experiment_columns(conn: sqlite3.Connection) -> None:
    for stmt in [
        "ALTER TABLE runs ADD COLUMN experiment_name TEXT",
        "ALTER TABLE runs ADD COLUMN verdict TEXT",
    ]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS projects (
          project_id TEXT PRIMARY KEY,
          label TEXT NOT NULL,
          orchestration_kit_root TEXT NOT NULL,
          project_root TEXT NOT NULL,
          registered_at TEXT,
          updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS runs (
          project_id TEXT NOT NULL,
          run_id TEXT NOT NULL,
          parent_run_id TEXT,
          kit TEXT,
          phase TEXT,
          started_at TEXT,
          finished_at TEXT,
          exit_code INTEGER,
          status TEXT,
          capsule_path TEXT,
          manifest_path TEXT,
          log_path TEXT,
          events_path TEXT,
          cwd TEXT,
          project_root TEXT,
          orchestration_kit_root TEXT,
          agent_runtime TEXT,
          host TEXT,
          pid INTEGER,
          reasoning TEXT,
          PRIMARY KEY(project_id, run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_runs_project_started
          ON runs(project_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_runs_project_status
          ON runs(project_id, status);
        CREATE INDEX IF NOT EXISTS idx_runs_project_parent
          ON runs(project_id, parent_run_id);

        CREATE TABLE IF NOT EXISTS requests (
          project_id TEXT NOT NULL,
          request_id TEXT NOT NULL,
          parent_run_id TEXT,
          child_run_id TEXT,
          from_kit TEXT,
          from_phase TEXT,
          to_kit TEXT,
          to_phase TEXT,
          action TEXT,
          status TEXT,
          request_path TEXT,
          response_path TEXT,
          enqueued_ts TEXT,
          completed_ts TEXT,
          reasoning TEXT,
          PRIMARY KEY(project_id, request_id)
        );

        CREATE INDEX IF NOT EXISTS idx_requests_project_parent
          ON requests(project_id, parent_run_id);
        CREATE INDEX IF NOT EXISTS idx_requests_project_child
          ON requests(project_id, child_run_id);
        """
    )
    _migrate_reasoning_columns(conn)
    _migrate_experiment_columns(conn)
