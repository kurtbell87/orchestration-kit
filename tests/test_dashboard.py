from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
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
        master_kit_root = base / name / "master-kit"
        (master_kit_root / "tools").mkdir(parents=True, exist_ok=True)
        (master_kit_root / "runs").mkdir(parents=True, exist_ok=True)

        # register command only validates this file exists
        (master_kit_root / "tools" / "kit").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

        run_id = f"{name}-run"
        run_root = master_kit_root / "runs" / run_id
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
        return master_kit_root, project_root, run_id

    def test_filtered_index_keeps_other_projects(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = os.environ.copy()
            env["MASTER_KIT_DASHBOARD_HOME"] = str(root / "dashboard-home")

            mk1, pr1, _ = self._write_fake_project(root, "one", ts="2026-02-13T00:00:00Z")
            mk2, pr2, _ = self._write_fake_project(root, "two", ts="2026-02-13T00:00:01Z")

            reg1 = self._run(
                [str(DASH), "register", "--master-kit-root", str(mk1), "--project-root", str(pr1), "--label", "one"],
                cwd=ROOT,
                env=env,
            )
            self.assertEqual(reg1.returncode, 0, reg1.stdout + reg1.stderr)
            p1 = json.loads(reg1.stdout)

            reg2 = self._run(
                [str(DASH), "register", "--master-kit-root", str(mk2), "--project-root", str(pr2), "--label", "two"],
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
            env["MASTER_KIT_DASHBOARD_HOME"] = str(Path(td) / "dashboard-home")
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
            old_home = os.environ.get("MASTER_KIT_DASHBOARD_HOME")
            os.environ["MASTER_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project(root, "artifact", ts="2026-02-13T00:00:00Z")
                record = dashboard_tool.upsert_registry_project(master_kit_root=mk1, project_root=pr1, label="artifact")
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
                    os.environ.pop("MASTER_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["MASTER_KIT_DASHBOARD_HOME"] = old_home

    def test_artifact_payload_project_scope_reads_project_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("MASTER_KIT_DASHBOARD_HOME")
            os.environ["MASTER_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, _ = self._write_fake_project(root, "projectscope", ts="2026-02-13T00:00:00Z")
                record = dashboard_tool.upsert_registry_project(master_kit_root=mk1, project_root=pr1, label="projectscope")
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
                    os.environ.pop("MASTER_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["MASTER_KIT_DASHBOARD_HOME"] = old_home

    def test_project_docs_payload_includes_required_docs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("MASTER_KIT_DASHBOARD_HOME")
            os.environ["MASTER_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, _ = self._write_fake_project(root, "projectdocs", ts="2026-02-13T00:00:00Z")
                record = dashboard_tool.upsert_registry_project(master_kit_root=mk1, project_root=pr1, label="projectdocs")
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                dashboard_tool.index_projects(prepared)

                (pr1 / "LAST_TOUCH.md").write_text("# Last Touch\n", encoding="utf-8")
                (pr1 / "DOMAIN_PRIORS.md").write_text("# Domain Priors\n", encoding="utf-8")
                (pr1 / "CONSTRUCTION_LOG.md").write_text("# Construction Log\n", encoding="utf-8")
                (pr1 / "docs").mkdir(parents=True, exist_ok=True)
                (pr1 / "docs" / "notes.md").write_text("# Notes\n", encoding="utf-8")

                mk_last_touch = mk1 / "claude-tdd-kit" / "LAST_TOUCH.md"
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
                self.assertTrue(by_key[("master-kit", "claude-tdd-kit/LAST_TOUCH.md")].get("exists"))
            finally:
                if old_home is None:
                    os.environ.pop("MASTER_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["MASTER_KIT_DASHBOARD_HOME"] = old_home


    def _write_fake_project_with_reasoning(
        self, base: Path, name: str, *, ts: str, reasoning: str | None
    ) -> tuple[Path, Path, str]:
        project_root = base / name / "project"
        master_kit_root = base / name / "master-kit"
        (master_kit_root / "tools").mkdir(parents=True, exist_ok=True)
        (master_kit_root / "runs").mkdir(parents=True, exist_ok=True)
        (master_kit_root / "tools" / "kit").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

        run_id = f"{name}-run"
        run_root = master_kit_root / "runs" / run_id
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
        return master_kit_root, project_root, run_id

    def test_reasoning_stored_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("MASTER_KIT_DASHBOARD_HOME")
            os.environ["MASTER_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project_with_reasoning(
                    root, "reasoning_test", ts="2026-02-14T00:00:00Z",
                    reasoning="Testing reasoning propagation",
                )
                record = dashboard_tool.upsert_registry_project(
                    master_kit_root=mk1, project_root=pr1, label="reasoning_test"
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
                    os.environ.pop("MASTER_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["MASTER_KIT_DASHBOARD_HOME"] = old_home

    def test_reasoning_null_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("MASTER_KIT_DASHBOARD_HOME")
            os.environ["MASTER_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project(
                    root, "no_reasoning", ts="2026-02-14T00:00:00Z"
                )
                record = dashboard_tool.upsert_registry_project(
                    master_kit_root=mk1, project_root=pr1, label="no_reasoning"
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
                    os.environ.pop("MASTER_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["MASTER_KIT_DASHBOARD_HOME"] = old_home

    def test_reasoning_in_dag_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("MASTER_KIT_DASHBOARD_HOME")
            os.environ["MASTER_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project_with_reasoning(
                    root, "dag_reasoning", ts="2026-02-14T00:00:00Z",
                    reasoning="DAG test reasoning",
                )
                record = dashboard_tool.upsert_registry_project(
                    master_kit_root=mk1, project_root=pr1, label="dag_reasoning"
                )
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                dashboard_tool.index_projects(prepared)

                dag = dashboard_tool.dag_payload(record["project_id"])
                self.assertTrue(len(dag["nodes"]) > 0)
                node = dag["nodes"][0]
                self.assertEqual(node["reasoning"], "DAG test reasoning")
            finally:
                if old_home is None:
                    os.environ.pop("MASTER_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["MASTER_KIT_DASHBOARD_HOME"] = old_home

    def test_request_reasoning_stored_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("MASTER_KIT_DASHBOARD_HOME")
            os.environ["MASTER_KIT_DASHBOARD_HOME"] = str(dashboard_home)
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
                    master_kit_root=mk1, project_root=pr1, label="req_reasoning"
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
                    os.environ.pop("MASTER_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["MASTER_KIT_DASHBOARD_HOME"] = old_home

    def test_reasoning_in_list_runs_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dashboard_home = root / "dashboard-home"
            old_home = os.environ.get("MASTER_KIT_DASHBOARD_HOME")
            os.environ["MASTER_KIT_DASHBOARD_HOME"] = str(dashboard_home)
            try:
                mk1, pr1, run_id = self._write_fake_project_with_reasoning(
                    root, "list_reasoning", ts="2026-02-14T00:00:00Z",
                    reasoning="Listed reasoning",
                )
                record = dashboard_tool.upsert_registry_project(
                    master_kit_root=mk1, project_root=pr1, label="list_reasoning"
                )
                prepared = dashboard_tool.prepare_projects(dashboard_tool.maybe_seed_registry())
                dashboard_tool.index_projects(prepared)

                payload = dashboard_tool.list_runs_payload({"project_id": record["project_id"]})
                runs = payload.get("runs", [])
                self.assertTrue(len(runs) > 0)
                self.assertEqual(runs[0]["reasoning"], "Listed reasoning")
            finally:
                if old_home is None:
                    os.environ.pop("MASTER_KIT_DASHBOARD_HOME", None)
                else:
                    os.environ["MASTER_KIT_DASHBOARD_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
