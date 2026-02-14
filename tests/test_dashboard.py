from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "tools" / "dashboard"


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


if __name__ == "__main__":
    unittest.main()
