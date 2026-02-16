from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPSULE_VALIDATOR = ROOT / "tools" / "validate-capsules"
MANIFEST_VALIDATOR = ROOT / "tools" / "validate-manifests"


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=False)


class CapsuleValidatorTests(unittest.TestCase):
    def test_capsule_validator_passes_for_valid_capsule(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            capsule = base / "runs" / "r1" / "capsules" / "ok.md"
            capsule.parent.mkdir(parents=True, exist_ok=True)
            capsule.write_text(
                "\n".join(
                    [
                        "Goal: Example goal.",
                        "What happened: Example happened.",
                        "Current status: ok.",
                        "Next action requested (exactly one): Do exactly one thing.",
                        "Evidence pointers:",
                        "- log: runs/r1/logs/x.log",
                        "If blocked: error signature + where to find full trace: none",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            proc = run([str(CAPSULE_VALIDATOR), str(base / "runs")], ROOT)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_capsule_validator_fails_for_long_capsule(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            capsule = base / "runs" / "r2" / "capsules" / "bad.md"
            capsule.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"L{i}" for i in range(35)]
            capsule.write_text("\n".join(lines) + "\n", encoding="utf-8")

            proc = run([str(CAPSULE_VALIDATOR), str(base / "runs")], ROOT)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("exceeds max 30", proc.stdout + proc.stderr)


class ManifestValidatorTests(unittest.TestCase):
    def test_manifest_validator_passes_for_valid_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            run_root = base / "runs" / "r3"
            manifest = run_root / "manifests" / "x.json"
            capsule = run_root / "capsules" / "x.md"
            log = run_root / "logs" / "x.log"

            manifest.parent.mkdir(parents=True, exist_ok=True)
            capsule.parent.mkdir(parents=True, exist_ok=True)
            log.parent.mkdir(parents=True, exist_ok=True)
            capsule.write_text("capsule\n", encoding="utf-8")
            log.write_text("log\n", encoding="utf-8")

            payload = {
                "metadata": {
                    "run_id": "r3",
                    "parent_run_id": None,
                    "kit": "research",
                    "phase": "status",
                    "started_at": "2026-02-13T00:00:00Z",
                    "finished_at": "2026-02-13T00:00:01Z",
                    "exit_code": 0,
                    "command": ["./experiment.sh", "status"],
                    "cwd": "research-kit",
                },
                "artifact_index": {
                    "tracked": [
                        {
                            "path": "runs/r3/logs/x.log",
                            "kind": "text",
                            "bytes": 4,
                            "sha256": "a" * 64,
                        }
                    ],
                    "omitted": {"files": 0, "bytes": 0},
                    "limits": {"max_files": 400, "max_total_bytes": 20000000},
                },
                "truth_pointers": [],
                "log_pointers": [
                    {
                        "path": "runs/r3/logs/x.log",
                        "kind": "phase_log",
                        "hint": "tail -n 200 runs/r3/logs/x.log",
                    }
                ],
                "capsule_path": "runs/r3/capsules/x.md",
            }
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            proc = run(
                [str(MANIFEST_VALIDATOR), "--check-files", str(base / "runs")],
                base,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_manifest_validator_fails_for_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            manifest = base / "runs" / "r4" / "manifests" / "bad.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({"metadata": {"run_id": "r4"}}), encoding="utf-8")

            proc = run([str(MANIFEST_VALIDATOR), str(base / "runs")], ROOT)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("missing", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
