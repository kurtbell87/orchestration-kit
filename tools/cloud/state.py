"""Project-local cloud state — tracks active runs in .kit/cloud-state.json.

Provides per-project visibility into which cloud instances are running,
complementing the global ~/.orchestration-kit-cloud/runs/ state.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


STATE_FILENAME = ".kit/cloud-state.json"


def _state_path(project_root: str) -> Path:
    return Path(project_root) / STATE_FILENAME


def _load(project_root: str) -> dict:
    """Load state file; return empty structure on missing or corrupt JSON."""
    path = _state_path(project_root)
    if not path.exists():
        return {"active_runs": {}}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or "active_runs" not in data:
            return {"active_runs": {}}
        return data
    except (json.JSONDecodeError, OSError):
        return {"active_runs": {}}


def _save(project_root: str, data: dict) -> None:
    """Atomic write via temp file + rename."""
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def register_run(
    project_root: str,
    run_id: str,
    *,
    instance_id: str,
    backend: str,
    instance_type: str,
    spec_file: Optional[str] = None,
    launched_at: Optional[str] = None,
    max_hours: float = 12.0,
) -> None:
    """Record a newly-provisioned run in project-local state."""
    data = _load(project_root)
    data["active_runs"][run_id] = {
        "instance_id": instance_id,
        "backend": backend,
        "instance_type": instance_type,
        "spec_file": spec_file or "",
        "launched_at": launched_at or datetime.now(timezone.utc).isoformat(),
        "max_hours": max_hours,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(project_root, data)


def remove_run(project_root: str, run_id: str) -> None:
    """Remove a run from project-local state (completed or terminated)."""
    data = _load(project_root)
    data["active_runs"].pop(run_id, None)
    _save(project_root, data)


def list_active_runs(project_root: str) -> list[dict]:
    """Return all active run entries with run_id included in each dict."""
    data = _load(project_root)
    results = []
    for rid, entry in data["active_runs"].items():
        results.append({"run_id": rid, **entry})
    return results


def get_run(project_root: str, run_id: str) -> Optional[dict]:
    """Return a single run entry or None."""
    data = _load(project_root)
    entry = data["active_runs"].get(run_id)
    if entry:
        return {"run_id": run_id, **entry}
    return None


def update_run(project_root: str, run_id: str, **updates) -> None:
    """Merge updates into an existing run entry. No-op if run_id not found."""
    data = _load(project_root)
    if run_id in data["active_runs"]:
        data["active_runs"][run_id].update(updates)
        _save(project_root, data)
