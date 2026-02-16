from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER_HOOK = ROOT / ".claude" / "hooks" / "pre-tool-use.sh"
TDD_HOOK = ROOT / "tdd-kit" / ".claude" / "hooks" / "pre-tool-use.sh"


class HookReentryGuardTests(unittest.TestCase):
    def run_hook(
        self,
        script: Path,
        *,
        env_overrides: dict[str, str],
        tool: str = "Edit",
        payload: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        for key in (
            "TDD_PHASE",
            "EXP_PHASE",
            "MATH_PHASE",
            "ORCHESTRATION_KIT_ROOT",
            "MASTER_HOOK_ACTIVE",
            "MASTER_HOOK_DEBUG",
        ):
            env.pop(key, None)

        env.update(env_overrides)
        env["CLAUDE_TOOL_NAME"] = tool
        env["CLAUDE_TOOL_INPUT"] = json.dumps(payload or {"file_path": "tests/foo_test.py"})

        return subprocess.run(
            [str(script)],
            cwd=str(ROOT),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_no_orchestration_kit_reentry(self) -> None:
        shared_env = {
            "ORCHESTRATION_KIT_ROOT": str(ROOT),
            "MASTER_HOOK_DEBUG": "1",
            "TDD_PHASE": "green",
        }

        with self.subTest("delegation_happens_once"):
            proc = self.run_hook(TDD_HOOK, env_overrides={**shared_env, "MASTER_HOOK_ACTIVE": "0"})
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stderr.count("BLOCKED:"), 1, proc.stderr)
            self.assertEqual(proc.stderr.count("MASTER_HOOK: enter"), 1, proc.stderr)
            self.assertEqual(proc.stderr.count("MASTER_HOOK: dispatch tdd"), 1, proc.stderr)

        with self.subTest("master_dispatch_does_not_recurse"):
            proc = self.run_hook(MASTER_HOOK, env_overrides={**shared_env, "MASTER_HOOK_ACTIVE": "1"})
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stderr.count("BLOCKED:"), 1, proc.stderr)
            self.assertEqual(proc.stderr.count("MASTER_HOOK: enter"), 1, proc.stderr)
            self.assertEqual(proc.stderr.count("MASTER_HOOK: dispatch tdd"), 1, proc.stderr)


if __name__ == "__main__":
    unittest.main()
