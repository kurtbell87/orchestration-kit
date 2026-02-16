from __future__ import annotations

import json
import os
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "tools" / "kit"
PUMP = ROOT / "tools" / "pump"

KITS = ("tdd", "research", "math")


class CrossPhaseRoutingTests(unittest.TestCase):
    request_ids: list[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.request_ids = []

    @classmethod
    def tearDownClass(cls) -> None:
        for request_id in cls.request_ids:
            req = ROOT / "interop" / "requests" / f"{request_id}.json"
            rsp = ROOT / "interop" / "responses" / f"{request_id}.json"
            if req.exists():
                req.unlink()
            if rsp.exists():
                rsp.unlink()

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["ORCHESTRATION_KIT_DASHBOARD_AUTOSTART"] = "0"
        return subprocess.run(cmd, cwd=str(ROOT), env=env, text=True, capture_output=True, check=False)

    def _json_tail(self, proc: subprocess.CompletedProcess[str]) -> dict[str, object]:
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        self.assertTrue(lines, proc.stdout + proc.stderr)
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        self.fail(f"missing json output: {proc.stdout}\n{proc.stderr}")

    def _phase_for(self, kit: str) -> tuple[str, list[str]]:
        if kit == "tdd":
            return "watch", ["../README.md", "--resolve"]
        return "status", []

    def _action_for(self, kit: str) -> tuple[str, list[str]]:
        phase, args = self._phase_for(kit)
        return f"{kit}.{phase}", args

    def _run_parent(self, kit: str) -> dict[str, object]:
        phase, args = self._phase_for(kit)
        proc = self._run([str(KIT), "--json", kit, phase, *args])
        payload = self._json_tail(proc)
        self.assertEqual(payload.get("status"), "ok", payload)
        return payload

    def _request_and_pump(
        self,
        *,
        run_id: str,
        from_kit: str,
        from_phase: str,
        to_kit: str,
    ) -> dict[str, object]:
        request_id = f"rq-routing-{uuid.uuid4().hex[:10]}"
        self.request_ids.append(request_id)

        action, action_args = self._action_for(to_kit)
        action_flags = [f"--arg={arg}" for arg in action_args]

        req_proc = self._run(
            [
                str(KIT),
                "request",
                "--json",
                "--request-id",
                request_id,
                "--from",
                from_kit,
                "--from-phase",
                from_phase,
                "--to",
                to_kit,
                "--action",
                action,
                "--run-id",
                run_id,
                "--must-read",
                f"runs/{run_id}/capsules/{from_kit}_{from_phase}.md",
                "--must-read",
                f"runs/{run_id}/manifests/{from_kit}_{from_phase}.json",
                "--allowed-path",
                "runs/*/capsules/*.md",
                "--allowed-path",
                "runs/*/manifests/*.json",
                "--deliverable",
                "runs/*/capsules/*.md",
                "--deliverable",
                "runs/*/manifests/*.json",
                *action_flags,
            ]
        )
        self._json_tail(req_proc)

        pump_proc = self._run([str(PUMP), "--once", "--request", request_id, "--json"])
        return self._json_tail(pump_proc)

    def test_routing_matrix_all_kit_pairs(self) -> None:
        parents: dict[str, dict[str, object]] = {kit: self._run_parent(kit) for kit in KITS}

        for from_kit in KITS:
            parent = parents[from_kit]
            run_id = str(parent["run_id"])
            from_phase = str(parent["phase"])
            for to_kit in KITS:
                with self.subTest(from_kit=from_kit, to_kit=to_kit):
                    pump = self._request_and_pump(
                        run_id=run_id,
                        from_kit=from_kit,
                        from_phase=from_phase,
                        to_kit=to_kit,
                    )
                    self.assertEqual(pump.get("status"), "ok", pump)
                    self.assertIsInstance(pump.get("child_run_id"), str)

    def test_cycle_tdd_research_math_tdd(self) -> None:
        start = self._run_parent("tdd")
        run_id = str(start["run_id"])
        phase = str(start["phase"])

        hop1 = self._request_and_pump(
            run_id=run_id,
            from_kit="tdd",
            from_phase=phase,
            to_kit="research",
        )
        self.assertEqual(hop1.get("status"), "ok", hop1)

        hop1_child = str(hop1["child_run_id"])
        hop2 = self._request_and_pump(
            run_id=hop1_child,
            from_kit="research",
            from_phase="status",
            to_kit="math",
        )
        self.assertEqual(hop2.get("status"), "ok", hop2)

        hop2_child = str(hop2["child_run_id"])
        hop3 = self._request_and_pump(
            run_id=hop2_child,
            from_kit="math",
            from_phase="status",
            to_kit="tdd",
        )
        self.assertEqual(hop3.get("status"), "ok", hop3)
        self.assertIsInstance(hop3.get("child_run_id"), str)


if __name__ == "__main__":
    unittest.main()
