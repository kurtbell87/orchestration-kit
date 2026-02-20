from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
import signal
import socket
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "tools" / "dashboard"
_LOADER = SourceFileLoader("dashboard_tool", str(DASH))
_SPEC = importlib.util.spec_from_loader(_LOADER.name, _LOADER)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - defensive
    raise RuntimeError("failed to load tools/dashboard module")
dashboard_tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dashboard_tool)


class DashboardTests(unittest.TestCase):
    def _run(self, cmd: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _write_fake_project(self, base: Path, name: str, *, ts: str) -> tuple[Path, Path, str]:
        project_root = base / name / "project"
        orchestration_kit_root = base / name / "orchestration-kit"
        (orchestration_kit_root / "tools").mkdir(parents=True, exist_ok=True)
        (orchestration_kit_root / "runs").mkdir(parents=True, exist_ok=True)

        # register command only validates this file exists
        (orchestration_kit_root / "tools" / "kit").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

        run_id = f"{name}-run"
        run_root = orchestration_kit_root / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        events = [
            {
                "ts": ts,
                "event": "run_started",
                "run_id": run_id,
                "parent_run_id": None,
                "kit": "research",
                "phase": "status",
            },
            {
                "ts": ts,
                "event": "run_finished",
                "run_id": run_id,
                "kit": "research",
                "phase": "status",
                "exit_code": 0,
            },
        ]
        with (run_root / "events.jsonl").open("w", encoding="utf-8") as fh:
            for event in events:
                json.dump(event, fh, sort_keys=True)
                fh.write("\n")

        project_root.mkdir(parents=True, exist_ok=True)
        return orchestration_kit_root, project_root, run_id

    def test_filtered_index_keeps_other_projects(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = os.environ.copy()
            env["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(root / "dashboard-home")

            mk1, pr1, _ = self._write_fake_project(root, "one", ts="2026-02-13T00:00:00Z")
            mk2, pr2, _ = self._write_fake_project(root, "two", ts="2026-02-13T00:00:01Z")

            reg1 = self._run(
                [str(DASH), "register", "--orchestration-kit-root", str(mk1), "--project-root", str(pr1), "--label", "one"],
                cwd=ROOT,
                env=env,
            )
            self.assertEqual(reg1.returncode, 0, reg1.stdout + reg1.stderr)
            p1 = json.loads(reg1.stdout)

            reg2 = self._run(
                [str(DASH), "register", "--orchestration-kit-root", str(mk2), "--project-root", str(pr2), "--label", "two"],
                cwd=ROOT,
                env=env,
            )
            self.assertEqual(reg2.returncode, 0, reg2.stdout + reg2.stderr)
            p2 = json.loads(reg2.stdout)

            idx_all = self._run([str(DASH), "index"], cwd=ROOT, env=env)
            self.assertEqual(idx_all.returncode, 0, idx_all.stdout + idx_all.stderr)
            idx_all_payload = json.loads(idx_all.stdout)
            self.assertEqual(int(idx_all_payload.get("projects_indexed", -1)), 2)
            self.assertEqual(int(idx_all_payload.get("runs_indexed", -1)), 2)

            idx_one = self._run([str(DASH), "index", "--project-id", p1["project_id"]], cwd=ROOT, env=env)
            self.assertEqual(idx_one.returncode, 0, idx_one.stdout + idx_one.stderr)
            idx_one_payload = json.loads(idx_one.stdout)
            self.assertEqual(int(idx_one_payload.get("projects_indexed", -1)), 1)
            self.assertEqual(int(idx_one_payload.get("runs_indexed", -1)), 1)

            db_file = Path(idx_one_payload["db_path"])
            self.assertTrue(db_file.exists())

            conn = sqlite3.connect(str(db_file))
            try:
                project_rows = conn.execute("SELECT project_id FROM projects ORDER BY project_id").fetchall()
                self.assertEqual(sorted([row[0] for row in project_rows]), sorted([p1["project_id"], p2["project_id"]]))

                run_counts = conn.execute(
                    "SELECT project_id, COUNT(*) FROM runs GROUP BY project_id ORDER BY project_id"
                ).fetchall()
                by_project = {pid: count for pid, count in run_counts}
                self.assertEqual(by_project.get(p1["project_id"]), 1)
                self.assertEqual(by_project.get(p2["project_id"]), 1)
            finally:
                conn.close()

    def test_service_status_reports_stopped_when_unreachable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env = os.environ.copy()
            env["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(Path(td) / "dashboard-home")
            proc = self._run(
                [str(DASH), "service-status", "--host", "127.0.0.1", "--port", "1"],
                cwd=ROOT,
                env=env,
            )
            self.assertNotEqual(proc.returncode, 0)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload.get("status"), "stopped")

    def test_artifact_payload_reads_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project(root, "artifact", ts="2026-02-13T00:00:00Z")
                record = dashboard_tool.upsert_registry_project(orchestration_kit_root=mk1, project_root=pr1, label="artifact")
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                dashboard_tool.index_projects(prepared)

                capsule = mk1 / "runs" / run_id / "capsules" / "render.md"
                capsule.parent.mkdir(parents=True, exist_ok=True)
                capsule.write_text("# Capsule\n\n- item one\n", encoding="utf-8")

                payload = dashboard_tool.artifact_payload(
                    project_id=str(record["project_id"]),
                    raw_path=f"runs/{run_id}/capsules/render.md",
                )
                self.assertEqual(payload.get("kind"), "markdown")
                self.assertIn("Capsule", str(payload.get("text")))
                self.assertEqual(payload.get("path"), f"runs/{run_id}/capsules/render.md")
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home

    def test_artifact_payload_project_scope_reads_project_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, _ = self._write_fake_project(root, "projectscope", ts="2026-02-13T00:00:00Z")
                record = dashboard_tool.upsert_registry_project(orchestration_kit_root=mk1, project_root=pr1, label="projectscope")
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                dashboard_tool.index_projects(prepared)

                last_touch = pr1 / "LAST_TOUCH.md"
                last_touch.write_text("# Last Touch\n\n- updated now\n", encoding="utf-8")

                payload = dashboard_tool.artifact_payload(
                    project_id=str(record["project_id"]),
                    raw_path="LAST_TOUCH.md",
                    scope="project",
                )
                self.assertEqual(payload.get("kind"), "markdown")
                self.assertIn("updated now", str(payload.get("text")))
                self.assertEqual(payload.get("path"), "LAST_TOUCH.md")

                prefixed = dashboard_tool.artifact_payload(
                    project_id=str(record["project_id"]),
                    raw_path="project/LAST_TOUCH.md",
                    scope="auto",
                )
                self.assertEqual(prefixed.get("path"), "LAST_TOUCH.md")
                self.assertIn("Last Touch", str(prefixed.get("text")))
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home

    def test_project_docs_payload_includes_required_docs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, _ = self._write_fake_project(root, "projectdocs", ts="2026-02-13T00:00:00Z")
                record = dashboard_tool.upsert_registry_project(orchestration_kit_root=mk1, project_root=pr1, label="projectdocs")
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                dashboard_tool.index_projects(prepared)

                (pr1 / "LAST_TOUCH.md").write_text("# Last Touch\n", encoding="utf-8")
                (pr1 / "DOMAIN_PRIORS.md").write_text("# Domain Priors\n", encoding="utf-8")
                (pr1 / "CONSTRUCTION_LOG.md").write_text("# Construction Log\n", encoding="utf-8")
                (pr1 / "docs").mkdir(parents=True, exist_ok=True)
                (pr1 / "docs" / "notes.md").write_text("# Notes\n", encoding="utf-8")

                mk_last_touch = mk1 / "tdd-kit" / "LAST_TOUCH.md"
                mk_last_touch.parent.mkdir(parents=True, exist_ok=True)
                mk_last_touch.write_text("# Kit Last Touch\n", encoding="utf-8")

                payload = dashboard_tool.project_docs_payload(str(record["project_id"]))
                docs = payload.get("docs")
                self.assertIsInstance(docs, list)

                by_key = {(str(d.get("scope")), str(d.get("path"))): d for d in docs if isinstance(d, dict)}
                self.assertTrue(by_key[("project", "LAST_TOUCH.md")].get("exists"))
                self.assertTrue(by_key[("project", "DOMAIN_PRIORS.md")].get("exists"))
                self.assertTrue(by_key[("project", "CONSTRUCTION_LOG.md")].get("exists"))
                self.assertTrue(by_key[("project", "docs/notes.md")].get("exists"))
                self.assertTrue(by_key[("orchestration-kit", "tdd-kit/LAST_TOUCH.md")].get("exists"))
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home


    def _write_fake_project_with_reasoning(
        self, base: Path, name: str, *, ts: str, reasoning: str | None
    ) -> tuple[Path, Path, str]:
        project_root = base / name / "project"
        orchestration_kit_root = base / name / "orchestration-kit"
        (orchestration_kit_root / "tools").mkdir(parents=True, exist_ok=True)
        (orchestration_kit_root / "runs").mkdir(parents=True, exist_ok=True)
        (orchestration_kit_root / "tools" / "kit").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

        run_id = f"{name}-run"
        run_root = orchestration_kit_root / "runs" / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        events = [
            {
                "ts": ts,
                "event": "run_started",
                "run_id": run_id,
                "parent_run_id": None,
                "kit": "research",
                "phase": "status",
                "reasoning": reasoning,
            },
            {
                "ts": ts,
                "event": "run_finished",
                "run_id": run_id,
                "kit": "research",
                "phase": "status",
                "exit_code": 0,
            },
        ]
        with (run_root / "events.jsonl").open("w", encoding="utf-8") as fh:
            for event in events:
                json.dump(event, fh, sort_keys=True)
                fh.write("\n")

        project_root.mkdir(parents=True, exist_ok=True)
        return orchestration_kit_root, project_root, run_id

    def test_reasoning_stored_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project_with_reasoning(
                    root, "reasoning_test", ts="2026-02-14T00:00:00Z",
                    reasoning="Testing reasoning propagation",
                )
                record = dashboard_tool.upsert_registry_project(
                    orchestration_kit_root=mk1, project_root=pr1, label="reasoning_test"
                )
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                result = dashboard_tool.index_projects(prepared)

                db_file = Path(result["db_path"])
                conn = sqlite3.connect(str(db_file))
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT reasoning FROM runs WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row["reasoning"], "Testing reasoning propagation")
                finally:
                    conn.close()
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home

    def test_reasoning_null_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project(
                    root, "no_reasoning", ts="2026-02-14T00:00:00Z"
                )
                record = dashboard_tool.upsert_registry_project(
                    orchestration_kit_root=mk1, project_root=pr1, label="no_reasoning"
                )
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                result = dashboard_tool.index_projects(prepared)

                db_file = Path(result["db_path"])
                conn = sqlite3.connect(str(db_file))
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT reasoning FROM runs WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertIsNone(row["reasoning"])
                finally:
                    conn.close()
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home

    def test_reasoning_in_dag_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project_with_reasoning(
                    root, "dag_reasoning", ts="2026-02-14T00:00:00Z",
                    reasoning="DAG test reasoning",
                )
                record = dashboard_tool.upsert_registry_project(
                    orchestration_kit_root=mk1, project_root=pr1, label="dag_reasoning"
                )
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                dashboard_tool.index_projects(prepared)

                dag = dashboard_tool.dag_payload(record["project_id"])
                self.assertTrue(len(dag["nodes"]) > 0)
                node = dag["nodes"][0]
                self.assertEqual(node["reasoning"], "DAG test reasoning")
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home

    def test_request_reasoning_stored_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project(
                    root, "req_reasoning", ts="2026-02-14T00:00:00Z"
                )
                # Write a request_enqueued event with reasoning
                run_root = mk1 / "runs" / run_id
                with (run_root / "events.jsonl").open("a", encoding="utf-8") as fh:
                    event = {
                        "ts": "2026-02-14T00:01:00Z",
                        "event": "request_enqueued",
                        "request_id": "rq-test-001",
                        "from_kit": "research",
                        "from_phase": "status",
                        "to_kit": "math",
                        "to_phase": "survey",
                        "action": "math.survey",
                        "reasoning": "Cross-kit handoff for formalization",
                    }
                    json.dump(event, fh, sort_keys=True)
                    fh.write("\n")

                record = dashboard_tool.upsert_registry_project(
                    orchestration_kit_root=mk1, project_root=pr1, label="req_reasoning"
                )
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                result = dashboard_tool.index_projects(prepared)

                db_file = Path(result["db_path"])
                conn = sqlite3.connect(str(db_file))
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT reasoning FROM requests WHERE request_id = ?",
                        ("rq-test-001",),
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row["reasoning"], "Cross-kit handoff for formalization")
                finally:
                    conn.close()
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home

    def test_reasoning_in_list_runs_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project_with_reasoning(
                    root, "list_reasoning", ts="2026-02-14T00:00:00Z",
                    reasoning="Listed reasoning",
                )
                record = dashboard_tool.upsert_registry_project(
                    orchestration_kit_root=mk1, project_root=pr1, label="list_reasoning"
                )
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                dashboard_tool.index_projects(prepared)

                payload = dashboard_tool.list_runs_payload({"project_id": record["project_id"]})
                runs = payload.get("runs", [])
                self.assertTrue(len(runs) > 0)
                self.assertEqual(runs[0]["reasoning"], "Listed reasoning")
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home


    def test_upsert_single_run_at_start(self) -> None:
        """upsert_single_run makes a running run visible immediately."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, _ = self._write_fake_project(root, "upsert", ts="2026-02-19T00:00:00Z")
                record = dashboard_tool.upsert_registry_project(
                    orchestration_kit_root=mk1, project_root=pr1, label="upsert"
                )

                # Write a run that has only run_started + phase_started (no run_finished).
                run_id = "upsert-running-run"
                run_root = mk1 / "runs" / run_id
                run_root.mkdir(parents=True, exist_ok=True)
                events = [
                    {
                        "ts": "2026-02-19T00:01:00Z",
                        "event": "run_started",
                        "run_id": run_id,
                        "parent_run_id": None,
                        "kit": "research",
                        "phase": "cycle",
                        "host": socket.gethostname(),
                        "pid": os.getpid(),
                    },
                    {
                        "ts": "2026-02-19T00:01:01Z",
                        "event": "phase_started",
                        "run_id": run_id,
                        "kit": "research",
                        "phase": "cycle",
                    },
                ]
                with (run_root / "events.jsonl").open("w", encoding="utf-8") as fh:
                    for event in events:
                        json.dump(event, fh, sort_keys=True)
                        fh.write("\n")

                # Upsert the single run.
                result = dashboard_tool.upsert_single_run(
                    project_id=str(record["project_id"]),
                    orchestration_kit_root=str(mk1),
                    project_root=str(pr1),
                    run_id=run_id,
                    run_root=run_root,
                )
                self.assertEqual(result["run_id"], run_id)
                self.assertEqual(result["status"], "running")

                # Verify it's in the DB.
                db_file = Path(result["db_path"])
                conn = sqlite3.connect(str(db_file))
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT status, kit, phase FROM runs WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row["status"], "running")
                    self.assertEqual(row["kit"], "research")
                    self.assertEqual(row["phase"], "cycle")
                finally:
                    conn.close()
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home

    def test_upsert_single_run_completion_updates(self) -> None:
        """upsert_single_run updates a running run to ok/failed on completion."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, _ = self._write_fake_project(root, "upsert2", ts="2026-02-19T00:00:00Z")
                record = dashboard_tool.upsert_registry_project(
                    orchestration_kit_root=mk1, project_root=pr1, label="upsert2"
                )

                run_id = "upsert-complete-run"
                run_root = mk1 / "runs" / run_id
                run_root.mkdir(parents=True, exist_ok=True)

                # First: write running events and upsert.
                events_running = [
                    {"ts": "2026-02-19T00:02:00Z", "event": "run_started", "run_id": run_id, "kit": "tdd", "phase": "full"},
                    {"ts": "2026-02-19T00:02:01Z", "event": "phase_started", "run_id": run_id, "kit": "tdd", "phase": "full"},
                ]
                with (run_root / "events.jsonl").open("w", encoding="utf-8") as fh:
                    for event in events_running:
                        json.dump(event, fh, sort_keys=True)
                        fh.write("\n")

                dashboard_tool.upsert_single_run(
                    project_id=str(record["project_id"]),
                    orchestration_kit_root=str(mk1),
                    project_root=str(pr1),
                    run_id=run_id,
                    run_root=run_root,
                )

                # Second: append completion events and upsert again.
                events_done = [
                    {"ts": "2026-02-19T00:05:00Z", "event": "run_finished", "run_id": run_id, "kit": "tdd", "phase": "full", "exit_code": 0},
                ]
                with (run_root / "events.jsonl").open("a", encoding="utf-8") as fh:
                    for event in events_done:
                        json.dump(event, fh, sort_keys=True)
                        fh.write("\n")

                result = dashboard_tool.upsert_single_run(
                    project_id=str(record["project_id"]),
                    orchestration_kit_root=str(mk1),
                    project_root=str(pr1),
                    run_id=run_id,
                    run_root=run_root,
                )
                self.assertEqual(result["status"], "ok")

                # Verify DB shows ok now.
                db_file = Path(result["db_path"])
                conn = sqlite3.connect(str(db_file))
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT status, exit_code FROM runs WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row["status"], "ok")
                    self.assertEqual(row["exit_code"], 0)
                finally:
                    conn.close()
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home

    def test_is_orphaned_for_dead_pid(self) -> None:
        """list_runs_payload marks runs with dead PIDs as is_orphaned=True."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, _ = self._write_fake_project(root, "orphan", ts="2026-02-19T00:00:00Z")
                record = dashboard_tool.upsert_registry_project(
                    orchestration_kit_root=mk1, project_root=pr1, label="orphan"
                )

                # Create a "running" run with a PID that doesn't exist.
                run_id = "orphan-test-run"
                run_root = mk1 / "runs" / run_id
                run_root.mkdir(parents=True, exist_ok=True)
                # Use a PID that's almost certainly dead (max PID - 1).
                dead_pid = 2147483646
                events = [
                    {
                        "ts": "2026-02-19T00:03:00Z",
                        "event": "run_started",
                        "run_id": run_id,
                        "kit": "research",
                        "phase": "frame",
                        "host": socket.gethostname(),
                        "pid": dead_pid,
                    },
                    {
                        "ts": "2026-02-19T00:03:01Z",
                        "event": "phase_started",
                        "run_id": run_id,
                        "kit": "research",
                        "phase": "frame",
                    },
                ]
                with (run_root / "events.jsonl").open("w", encoding="utf-8") as fh:
                    for event in events:
                        json.dump(event, fh, sort_keys=True)
                        fh.write("\n")

                dashboard_tool.upsert_single_run(
                    project_id=str(record["project_id"]),
                    orchestration_kit_root=str(mk1),
                    project_root=str(pr1),
                    run_id=run_id,
                    run_root=run_root,
                )

                payload = dashboard_tool.list_runs_payload({"project_id": record["project_id"], "status": "running"})
                runs = payload.get("runs", [])
                orphan_runs = [r for r in runs if r["run_id"] == run_id]
                self.assertEqual(len(orphan_runs), 1)
                self.assertTrue(orphan_runs[0]["is_orphaned"])
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home

    def test_is_orphaned_false_for_live_pid(self) -> None:
        """list_runs_payload marks runs with live PIDs as is_orphaned=False."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("ORCHESTRATION_KIT_DASHBOARD_HOME")
            os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, _ = self._write_fake_project(root, "alive", ts="2026-02-19T00:00:00Z")
                record = dashboard_tool.upsert_registry_project(
                    orchestration_kit_root=mk1, project_root=pr1, label="alive"
                )

                # Create a "running" run with our own PID (definitely alive).
                run_id = "alive-test-run"
                run_root = mk1 / "runs" / run_id
                run_root.mkdir(parents=True, exist_ok=True)
                events = [
                    {
                        "ts": "2026-02-19T00:04:00Z",
                        "event": "run_started",
                        "run_id": run_id,
                        "kit": "tdd",
                        "phase": "full",
                        "host": socket.gethostname(),
                        "pid": os.getpid(),
                    },
                    {
                        "ts": "2026-02-19T00:04:01Z",
                        "event": "phase_started",
                        "run_id": run_id,
                        "kit": "tdd",
                        "phase": "full",
                    },
                ]
                with (run_root / "events.jsonl").open("w", encoding="utf-8") as fh:
                    for event in events:
                        json.dump(event, fh, sort_keys=True)
                        fh.write("\n")

                dashboard_tool.upsert_single_run(
                    project_id=str(record["project_id"]),
                    orchestration_kit_root=str(mk1),
                    project_root=str(pr1),
                    run_id=run_id,
                    run_root=run_root,
                )

                payload = dashboard_tool.list_runs_payload({"project_id": record["project_id"], "status": "running"})
                runs = payload.get("runs", [])
                alive_runs = [r for r in runs if r["run_id"] == run_id]
                self.assertEqual(len(alive_runs), 1)
                self.assertFalse(alive_runs[0]["is_orphaned"])
            finally:
                if old_home is None:
                    os.environ.pop("ORCHESTRATION_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["ORCHESTRATION_KIT_DASHBOARD_HOME"] = old_home


class MCPActiveKillTests(unittest.TestCase):
    """Test kit.active and kit.kill tool handlers in-process (no HTTP)."""

    def _make_facade(self) -> "Any":
        import sys
        sys.path.insert(0, str(ROOT))
        from mcp.server import MasterKitFacade, ServerConfig

        config = ServerConfig(
            root=ROOT,
            host="127.0.0.1",
            port=0,
            token="test",
            max_output_bytes=32000,
            log_dir=ROOT / "runs" / "mcp-logs",
        )
        return MasterKitFacade(config)

    def test_kit_active_empty(self) -> None:
        facade = self._make_facade()
        result = facade.call_tool("kit.active", {})
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["processes"], [])

    def test_kit_active_shows_launched_process(self) -> None:
        facade = self._make_facade()
        # Launch a dummy process that sleeps
        proc = subprocess.Popen(
            ["sleep", "60"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        facade._background["test-run-001"] = proc
        try:
            result = facade.call_tool("kit.active", {})
            self.assertEqual(result["count"], 1)
            entry = result["processes"][0]
            self.assertEqual(entry["run_id"], "test-run-001")
            self.assertEqual(entry["pid"], proc.pid)
            self.assertEqual(entry["status"], "running")
            self.assertIsNone(entry["exit_code"])
        finally:
            proc.terminate()
            proc.wait()

    def test_kit_active_shows_finished_process(self) -> None:
        facade = self._make_facade()
        proc = subprocess.Popen(["true"])
        proc.wait()
        facade._background["test-run-done"] = proc

        result = facade.call_tool("kit.active", {})
        self.assertEqual(result["count"], 1)
        entry = result["processes"][0]
        self.assertEqual(entry["run_id"], "test-run-done")
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["exit_code"], 0)

    def test_kit_kill_terminates_process(self) -> None:
        facade = self._make_facade()
        proc = subprocess.Popen(
            ["sleep", "60"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        facade._background["test-kill-001"] = proc
        try:
            result = facade.call_tool("kit.kill", {"run_id": "test-kill-001"})
            self.assertEqual(result["result"], "signal_sent")
            self.assertEqual(result["signal"], "SIGTERM")
            self.assertEqual(result["pid"], proc.pid)
            # Wait for it to actually die
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            proc.wait()
            raise

    def test_kit_kill_already_finished(self) -> None:
        facade = self._make_facade()
        proc = subprocess.Popen(["true"])
        proc.wait()
        facade._background["test-kill-done"] = proc

        result = facade.call_tool("kit.kill", {"run_id": "test-kill-done"})
        self.assertEqual(result["result"], "already_finished")
        self.assertEqual(result["exit_code"], 0)

    def test_kit_kill_unknown_run_id(self) -> None:
        facade = self._make_facade()
        from mcp.server import MCPToolError
        with self.assertRaises(MCPToolError):
            facade.call_tool("kit.kill", {"run_id": "nonexistent"})


if __name__ == "__main__":
    unittest.main()
