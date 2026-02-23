"""Pre-flight validation for experiment scripts before cloud execution."""

import ast
import os
import subprocess
import sys


def syntax_check(script: str) -> tuple[bool, str]:
    """Check syntax of a Python script. Returns (ok, message)."""
    try:
        with open(script) as f:
            source = f.read()
        compile(source, script, "exec")
        return True, "Syntax OK"
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"
    except Exception as e:
        return False, str(e)


def import_check(script: str) -> tuple[bool, str]:
    """AST-parse the script, extract imports, verify each is importable."""
    try:
        with open(script) as f:
            tree = ast.parse(f.read())
    except SyntaxError as e:
        return False, f"Parse error: {e}"

    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split('.')[0])

    missing = []
    for mod in sorted(modules):
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"import {mod}"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                missing.append(mod)
        except subprocess.TimeoutExpired:
            missing.append(f"{mod} (timeout)")

    if missing:
        return False, f"Missing imports: {', '.join(missing)}"
    return True, f"All {len(modules)} imports OK"


def smoke_test(script: str, timeout: int = 300) -> tuple[bool, str]:
    """Run `python <script> --smoke-test` with timeout.

    The script should support --smoke-test flag that runs a minimal
    forward pass (1 batch, CPU/MPS) and exits.
    """
    try:
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
        result = subprocess.run(
            [sys.executable, script, "--smoke-test"],
            capture_output=True, text=True, timeout=timeout,
            env=env
        )
        if result.returncode == 0:
            return True, "Smoke test passed"
        err_lines = result.stderr.strip().split('\n')
        return False, "Smoke test failed:\n" + '\n'.join(err_lines[-20:])
    except subprocess.TimeoutExpired:
        return False, f"Smoke test timeout after {timeout}s"


def validate_all(script: str, skip_smoke: bool = False) -> tuple[bool, list[tuple[str, bool, str]]]:
    """Run all validation checks. Returns (all_passed, [(check_name, passed, message), ...])."""
    results = []

    ok, msg = syntax_check(script)
    results.append(("syntax", ok, msg))
    if not ok:
        return False, results

    ok, msg = import_check(script)
    results.append(("imports", ok, msg))
    if not ok:
        return False, results

    if not skip_smoke:
        ok, msg = smoke_test(script)
        results.append(("smoke_test", ok, msg))
        if not ok:
            return False, results

    return True, results
