"""Parallel batch dispatch for cloud-run: launch N experiments on separate EC2 instances."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import remote
from . import preflight


POLL_INTERVAL_SECONDS = 30
TERMINAL_STATUSES = {"completed", "failed", "error", "terminated"}


def _spec_output_dir(output_base: str | None, spec: str) -> str | None:
    """Derive per-spec output directory from output_base, or None."""
    if output_base:
        return str(Path(output_base) / Path(spec).stem)
    return None


def generate_batch_id() -> str:
    """Generate unique batch ID: batch-{YYYYMMDDTHHMMSSZ}-{8-hex-chars}."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"batch-{ts}-{short}"


def _batch_state_dir() -> Path:
    """Return ~/.orchestration-kit-cloud/batches/, creating if needed."""
    d = Path(os.path.expanduser("~/.orchestration-kit-cloud/batches"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_batch_state(batch_id: str, state: dict) -> Path:
    """Atomic write of batch state JSON."""
    path = _batch_state_dir() / f"{batch_id}.json"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def load_batch_state(batch_id: str) -> dict:
    """Load batch state. Raise FileNotFoundError if missing."""
    path = _batch_state_dir() / f"{batch_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No batch state found for {batch_id}")
    return json.loads(path.read_text())


def launch_batch(
    *,
    specs: list[str],
    command: str,
    backend,
    backend_name: str,
    project_root: str,
    instance_type: str,
    data_dirs: list[str] | None = None,
    sync_back: str = "results",
    output_base: str | None = None,
    use_spot: bool = True,
    max_hours: float = 12.0,
    env_vars: dict[str, str] | None = None,
    max_instances: int = 5,
    max_cost: float | None = None,
    gpu_mode: bool = False,
    image_tag: str | None = None,
) -> dict:
    """Launch N experiments in parallel on separate instances."""
    # 1. Validate count
    if len(specs) > max_instances:
        raise ValueError(
            f"Batch size {len(specs)} exceeds max_instances={max_instances}"
        )

    # 2. Cost guard
    if max_cost is not None:
        total_cost = 0.0
        for spec in specs:
            try:
                pf = preflight.check_spec(spec)
                cost_per_hour = pf.get("cost_per_hour_spot", pf.get("cost_per_hour", 0))
                wall_hours = pf.get("profile", {}).get("estimated_wall_hours", 0)
                total_cost += cost_per_hour * wall_hours
            except (ValueError, FileNotFoundError, KeyError):
                # Skip specs without compute profile
                pass
        if total_cost > max_cost:
            raise ValueError(
                f"Estimated total cost ${total_cost:.2f} exceeds max_cost=${max_cost:.2f}"
            )

    # 3. Generate batch_id
    batch_id = generate_batch_id()

    # 4. Launch each spec
    runs: dict[str, str] = {}
    for spec in specs:
        result = remote.run(
            command=command,
            backend=backend,
            backend_name=backend_name,
            project_root=project_root,
            spec_file=spec,
            instance_type=instance_type,
            data_dirs=data_dirs,
            sync_back=sync_back,
            local_results_dir=_spec_output_dir(output_base, spec),
            use_spot=use_spot,
            max_hours=max_hours,
            detach=True,
            env_vars=env_vars,
            gpu_mode=gpu_mode,
            image_tag=image_tag,
            batch_id=batch_id,
        )
        runs[spec] = result["run_id"]

    # 5. Save initial batch state
    state = {
        "batch_id": batch_id,
        "runs": runs,
        "specs": specs,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "max_instances": max_instances,
        "results": {},
    }
    save_batch_state(batch_id, state)

    # 6. Poll loop
    pending_runs = dict(runs)  # spec -> run_id for runs still active
    results: dict[str, dict] = {}
    first_poll = True

    while pending_runs:
        if first_poll:
            first_poll = False
        else:
            time.sleep(POLL_INTERVAL_SECONDS)
        for spec, run_id in list(pending_runs.items()):
            poll_result = remote.poll_status(run_id)
            status = poll_result.get("status", "running")
            if status in TERMINAL_STATUSES:
                results[spec] = poll_result
                if status == "completed":
                    try:
                        remote.pull_results(run_id, _spec_output_dir(output_base, spec))
                    except Exception:
                        pass
                del pending_runs[spec]
        if not pending_runs:
            break

    # 7. Determine final status
    all_completed = all(r.get("status") == "completed" for r in results.values())
    final_status = "completed" if all_completed else "partial"

    state["status"] = final_status
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    state["results"] = {spec: r.get("status", "unknown") for spec, r in results.items()}
    save_batch_state(batch_id, state)

    return state


def poll_batch(batch_id: str) -> dict:
    """Load batch state, poll each run, return updated state."""
    state = load_batch_state(batch_id)
    run_statuses: dict[str, dict] = {}

    for spec, run_id in state.get("runs", {}).items():
        try:
            poll_result = remote.poll_status(run_id)
            run_statuses[spec] = poll_result
        except Exception:
            run_statuses[spec] = {"run_id": run_id, "status": "unknown"}

    state["run_statuses"] = run_statuses
    return state


def pull_batch(batch_id: str, output_base: str | None = None) -> dict:
    """Pull results for all completed runs in a batch."""
    state = load_batch_state(batch_id)
    pulled: dict[str, str] = {}

    for spec, run_id in state.get("runs", {}).items():
        # Check if run is completed before pulling
        try:
            poll_result = remote.poll_status(run_id)
            if poll_result.get("status") != "completed":
                continue
        except Exception:
            continue

        try:
            result_dir = remote.pull_results(run_id, _spec_output_dir(output_base, spec))
            pulled[spec] = result_dir
        except Exception as e:
            pulled[spec] = f"error: {e}"

    return pulled


def list_batches() -> list[dict]:
    """Glob batch state dir for batch-*.json, return sorted most-recent-first."""
    state_dir = _batch_state_dir()
    batches = []
    for p in state_dir.glob("batch-*.json"):
        try:
            batches.append(json.loads(p.read_text()))
        except Exception:
            pass

    batches.sort(key=lambda b: b.get("started_at", ""), reverse=True)
    return batches
