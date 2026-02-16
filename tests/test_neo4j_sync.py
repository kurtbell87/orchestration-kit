"""Tests for dashboard.neo4j_sync using a mock Neo4j driver."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.schema import ensure_schema
from dashboard.neo4j_sync import (
    _merge_projects,
    _merge_runs,
    _merge_interop_edges,
    sync_project,
    sync_all,
)


def _seed_db(db_path: str) -> None:
    """Create a small SQLite database with test data."""
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    conn.execute(
        """
        INSERT INTO projects(project_id, label, orchestration_kit_root, project_root, registered_at, updated_at)
        VALUES('p1', 'test-project', '/mk', '/pr', '2026-02-14T00:00:00Z', '2026-02-14T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO runs(
          project_id, run_id, parent_run_id, kit, phase,
          started_at, finished_at, exit_code, status, reasoning
        )
        VALUES('p1', 'run-1', NULL, 'research', 'status',
               '2026-02-14T00:00:00Z', '2026-02-14T00:01:00Z', 0, 'ok', 'Initial survey')
        """
    )
    conn.execute(
        """
        INSERT INTO runs(
          project_id, run_id, parent_run_id, kit, phase,
          started_at, finished_at, exit_code, status, reasoning
        )
        VALUES('p1', 'run-2', 'run-1', 'math', 'survey',
               '2026-02-14T00:02:00Z', '2026-02-14T00:03:00Z', 0, 'ok', 'Cross-kit survey')
        """
    )
    conn.execute(
        """
        INSERT INTO requests(
          project_id, request_id, parent_run_id, child_run_id,
          from_kit, from_phase, to_kit, to_phase, action,
          status, reasoning
        )
        VALUES('p1', 'rq-001', 'run-1', 'run-2',
               'research', 'status', 'math', 'survey', 'math.survey',
               'ok', 'Needs formalization check')
        """
    )
    conn.commit()
    conn.close()


class MockTx:
    """Mock Neo4j transaction that records run() calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **kwargs: Any) -> None:
        self.calls.append((query.strip(), kwargs))


class MockSession:
    """Mock Neo4j session that captures write transactions."""

    def __init__(self) -> None:
        self.tx = MockTx()

    def execute_write(self, fn: Any) -> Any:
        return fn(self.tx)

    def __enter__(self) -> "MockSession":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class MockDriver:
    """Mock Neo4j driver."""

    def __init__(self) -> None:
        self._session = MockSession()

    def session(self) -> MockSession:
        return self._session

    def close(self) -> None:
        pass


class Neo4jSyncTests(unittest.TestCase):
    def test_merge_projects(self) -> None:
        tx = MockTx()
        rows = [
            {"project_id": "p1", "label": "test", "project_root": "/pr", "orchestration_kit_root": "/mk"},
        ]
        count = _merge_projects(tx, rows)
        self.assertEqual(count, 1)
        self.assertEqual(len(tx.calls), 1)
        self.assertIn("MERGE (p:Project", tx.calls[0][0])

    def test_merge_runs_creates_child_of_edge(self) -> None:
        tx = MockTx()
        rows = [
            {
                "run_id": "run-2",
                "kit": "math",
                "phase": "survey",
                "status": "ok",
                "exit_code": 0,
                "started_at": "2026-02-14T00:00:00Z",
                "finished_at": "2026-02-14T00:01:00Z",
                "reasoning": "test",
                "project_id": "p1",
                "parent_run_id": "run-1",
            },
        ]
        count = _merge_runs(tx, rows)
        self.assertEqual(count, 1)
        # Should have: MERGE Run, MERGE BELONGS_TO, MERGE CHILD_OF = 3 calls
        self.assertEqual(len(tx.calls), 3)
        child_of_calls = [c for c in tx.calls if "CHILD_OF" in c[0]]
        self.assertEqual(len(child_of_calls), 1)

    def test_merge_runs_no_child_of_for_root(self) -> None:
        tx = MockTx()
        rows = [
            {
                "run_id": "run-1",
                "kit": "research",
                "phase": "status",
                "status": "ok",
                "exit_code": 0,
                "started_at": "2026-02-14T00:00:00Z",
                "finished_at": "2026-02-14T00:01:00Z",
                "reasoning": None,
                "project_id": "p1",
                "parent_run_id": None,
            },
        ]
        count = _merge_runs(tx, rows)
        self.assertEqual(count, 1)
        # Should have: MERGE Run, MERGE BELONGS_TO = 2 calls (no CHILD_OF)
        self.assertEqual(len(tx.calls), 2)
        child_of_calls = [c for c in tx.calls if "CHILD_OF" in c[0]]
        self.assertEqual(len(child_of_calls), 0)

    def test_merge_interop_edges(self) -> None:
        tx = MockTx()
        rows = [
            {
                "parent_run_id": "run-1",
                "child_run_id": "run-2",
                "request_id": "rq-001",
                "action": "math.survey",
                "from_kit": "research",
                "from_phase": "status",
                "reasoning": "Needs formalization",
            },
        ]
        count = _merge_interop_edges(tx, rows)
        self.assertEqual(count, 1)
        self.assertIn("INTEROP", tx.calls[0][0])

    def test_merge_interop_skips_missing_ids(self) -> None:
        tx = MockTx()
        rows = [
            {
                "parent_run_id": "run-1",
                "child_run_id": None,
                "request_id": "rq-002",
                "action": "math.survey",
                "from_kit": "research",
                "from_phase": "status",
                "reasoning": None,
            },
        ]
        count = _merge_interop_edges(tx, rows)
        self.assertEqual(count, 0)
        self.assertEqual(len(tx.calls), 0)

    def test_sync_project_full(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_file = str(Path(td) / "test.db")
            _seed_db(db_file)

            driver = MockDriver()
            result = sync_project("p1", driver, db_file)

            self.assertEqual(result["projects_synced"], 1)
            self.assertEqual(result["runs_synced"], 2)
            self.assertEqual(result["interop_edges_synced"], 1)

    def test_sync_all_full(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_file = str(Path(td) / "test.db")
            _seed_db(db_file)

            driver = MockDriver()
            result = sync_all(driver, db_file)

            self.assertEqual(result["projects_synced"], 1)
            self.assertEqual(result["runs_synced"], 2)
            self.assertEqual(result["interop_edges_synced"], 1)

    def test_sync_project_nonexistent_returns_zeros(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_file = str(Path(td) / "test.db")
            _seed_db(db_file)

            driver = MockDriver()
            result = sync_project("nonexistent", driver, db_file)

            self.assertEqual(result["projects_synced"], 0)
            self.assertEqual(result["runs_synced"], 0)
            self.assertEqual(result["interop_edges_synced"], 0)

    def test_reasoning_propagated_to_neo4j(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_file = str(Path(td) / "test.db")
            _seed_db(db_file)

            driver = MockDriver()
            sync_project("p1", driver, db_file)

            tx = driver._session.tx
            # Find run merge calls and check reasoning is passed
            run_merge_calls = [
                c for c in tx.calls
                if "MERGE (r:Run" in c[0]
            ]
            self.assertEqual(len(run_merge_calls), 2)
            reasoning_values = [c[1].get("reasoning") for c in run_merge_calls]
            self.assertIn("Initial survey", reasoning_values)
            self.assertIn("Cross-kit survey", reasoning_values)

            # Check interop edge has reasoning
            interop_calls = [c for c in tx.calls if "INTEROP" in c[0]]
            self.assertEqual(len(interop_calls), 1)
            self.assertEqual(interop_calls[0][1].get("reasoning"), "Needs formalization check")


if __name__ == "__main__":
    unittest.main()
