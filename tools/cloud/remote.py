"""Remote execution orchestrator: upload → provision → execute → retrieve → terminate."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .backends.base import ComputeBackend, InstanceConfig
from .config import (
    DEFAULT_MAX_HOURS,
    POLL_INTERVAL_SECONDS,
    STATE_DIR,
    RESOURCE_TAGS,
    ECR_REPO_URI,
    EBS_DATA_SNAPSHOT_ID,
    IAM_INSTANCE_PROFILE,
)
from . import s3 as s3_helper
from . import state as project_state


class DuplicateSpecError(RuntimeError):
    """Raised when an instance is already running for the same spec file."""


def _state_dir() -> Path:
    d = Path(os.path.expanduser(STATE_DIR))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _generate_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"cloud-{ts}-{short}"


def _save_state(run_id: str, state: dict) -> Path:
    path = _state_dir() / f"{run_id}.json"
    path.write_text(json.dumps(state, indent=2))
    return path


def _load_state(run_id: str) -> dict:
    path = _state_dir() / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No state found for run {run_id}")
    return json.loads(path.read_text())


def _update_state(run_id: str, **updates) -> dict:
    state = _load_state(run_id)
    state.update(updates)
    _save_state(run_id, state)
    return state


# ---------------------------------------------------------------------------
# Main execution flow
# ---------------------------------------------------------------------------

def run(
    *,
    command: str,
    backend: ComputeBackend,
    backend_name: str,
    project_root: str,
    spec_file: Optional[str] = None,
    instance_type: str,
    data_dirs: Optional[list[str]] = None,
    sync_back: str = "results",
    local_results_dir: Optional[str] = None,
    use_spot: bool = True,
    max_hours: float = DEFAULT_MAX_HOURS,
    detach: bool = False,
    dry_run: bool = False,
    env_vars: Optional[dict[str, str]] = None,
    tags: Optional[dict[str, str]] = None,
    network_volume_id: Optional[str] = None,
    allow_duplicate: bool = False,
    image_tag: Optional[str] = None,
    gpu_mode: bool = False,
    batch_id: Optional[str] = None,
) -> dict:
    """Execute an experiment on a remote cloud instance.

    Returns a dict with run_id, status, instance_id, exit_code, etc.
    """
    run_id = _generate_run_id()
    s3_prefix = s3_helper.get_run_s3_prefix(run_id)

    state = {
        "run_id": run_id,
        "backend": backend_name,
        "instance_type": instance_type,
        "command": command,
        "spec_file": spec_file,
        "project_root": project_root,
        "data_dirs": data_dirs or [],
        "sync_back": sync_back,
        "local_results_dir": local_results_dir,
        "s3_prefix": s3_prefix,
        "use_spot": use_spot,
        "max_hours": max_hours,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "instance_id": None,
        "exit_code": None,
        "finished_at": None,
        "batch_id": batch_id or "",
    }

    if dry_run:
        state["status"] = "dry_run"
        _save_state(run_id, state)
        return state

    _save_state(run_id, state)

    try:
        # --- 0. Spec duplicate check ---
        if spec_file and not allow_duplicate and backend is not None:
            existing = backend.find_instances_by_spec(spec_file)
            if existing:
                _update_state(run_id, status="blocked_duplicate")
                ids = ", ".join(e["instance_id"] for e in existing)
                raise DuplicateSpecError(
                    f"Instance(s) already running for spec '{spec_file}': {ids}. "
                    "Use --allow-duplicate to override."
                )

        # --- 1. Resolve image URI or upload code ---
        image_uri = None
        ebs_snapshot_id = None
        iam_instance_profile = None

        if gpu_mode and backend_name == "aws":
            # GPU mode: no Docker, run directly on PyTorch DL AMI
            image_uri = None
            ebs_snapshot_id = EBS_DATA_SNAPSHOT_ID or None
            iam_instance_profile = IAM_INSTANCE_PROFILE or None
            print(f"[{run_id}] GPU mode: PyTorch Deep Learning AMI (no Docker)")
            if ebs_snapshot_id:
                print(f"[{run_id}] EBS data snapshot: {ebs_snapshot_id}")
        elif ECR_REPO_URI and backend_name == "aws":
            # ECR/EBS path: skip code upload, use pre-built Docker image
            tag = image_tag or "latest"
            image_uri = f"{ECR_REPO_URI}:{tag}"
            ebs_snapshot_id = EBS_DATA_SNAPSHOT_ID or None
            iam_instance_profile = IAM_INSTANCE_PROFILE or None
            print(f"[{run_id}] Using ECR image: {image_uri}")
            if ebs_snapshot_id:
                print(f"[{run_id}] EBS data snapshot: {ebs_snapshot_id}")
        else:
            # Legacy path: upload code tarball to S3
            print(f"[{run_id}] Uploading code to S3...")
            s3_helper.upload_code(project_root, run_id)

        if data_dirs:
            print(f"[{run_id}] Uploading extra data dirs: {data_dirs}")
            s3_helper.upload_dirs(data_dirs, run_id)

        # --- 2. Provision instance ---
        print(f"[{run_id}] Provisioning {backend_name} {instance_type}...")
        config = InstanceConfig(
            instance_type=instance_type,
            run_id=run_id,
            s3_prefix=s3_prefix,
            command=command,
            max_hours=max_hours,
            use_spot=use_spot,
            env_vars=env_vars or {},
            tags={**(tags or {}), "SpecFile": spec_file or ""},
            network_volume_id=network_volume_id,
            image_uri=image_uri,
            ebs_snapshot_id=ebs_snapshot_id,
            iam_instance_profile=iam_instance_profile,
            gpu_mode=gpu_mode,
        )
        instance_id = backend.provision(config)
        _update_state(run_id, instance_id=instance_id, status="provisioning")
        print(f"[{run_id}] Instance launched: {instance_id}")

        # --- Register in project-local state ---
        project_state.register_run(
            project_root, run_id,
            instance_id=instance_id,
            backend=backend_name,
            instance_type=instance_type,
            spec_file=spec_file,
            launched_at=config.launched_at,
            max_hours=max_hours,
            batch_id=batch_id,
        )

        # --- 3. Wait for ready ---
        wait_timeout = max(900, int(max_hours * 3600))
        print(f"[{run_id}] Waiting for instance to be ready (timeout {wait_timeout}s)...")
        backend.wait_ready(instance_id, timeout=wait_timeout)
        _update_state(run_id, status="running")
        print(f"[{run_id}] Instance ready. Experiment executing remotely.")

        # --- Detach mode: save state and return ---
        if detach:
            print(f"[{run_id}] Detached. Use these commands to check status and retrieve results:")
            print(f"  tools/cloud-run status {run_id}")
            print(f"  tools/cloud-run pull {run_id}")
            return _load_state(run_id)

        # --- 4. Poll for completion ---
        return _poll_and_retrieve(run_id, backend, instance_id, local_results_dir, sync_back)

    except Exception as e:
        _update_state(run_id, status="error", error=str(e))
        # Attempt cleanup — use _load_state to get the persisted instance_id
        saved = _load_state(run_id)
        if saved.get("instance_id"):
            try:
                backend.terminate(saved["instance_id"])
            except Exception:
                pass
        raise


def poll_status(run_id: str) -> dict:
    """Check the status of a detached run by polling S3 for exit_code."""
    state = _load_state(run_id)

    if state["status"] in ("completed", "failed", "error"):
        return state

    exit_code = s3_helper.check_exit_code(run_id)
    if exit_code is not None:
        new_status = "completed" if exit_code == 0 else "failed"
        state = _update_state(
            run_id,
            exit_code=exit_code,
            status=new_status,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    return state


def pull_results(run_id: str, local_dir: Optional[str] = None) -> str:
    """Download results for a completed run."""
    state = _load_state(run_id)
    sync_back = state.get("sync_back", "results")

    if local_dir is None:
        local_dir = state.get("local_results_dir")
    if local_dir is None:
        local_dir = f"cloud-results/{run_id}"

    print(f"[{run_id}] Downloading results to {local_dir}...")
    s3_helper.download_results(run_id, local_dir, remote_subdir=sync_back)
    _update_state(run_id, local_results_dir=local_dir)
    print(f"[{run_id}] Results downloaded to: {local_dir}")
    return local_dir


def terminate_run(run_id: str, backend: ComputeBackend, project_root: Optional[str] = None) -> None:
    """Terminate a running instance for a given run."""
    state = _load_state(run_id)
    instance_id = state.get("instance_id")
    if instance_id:
        print(f"[{run_id}] Terminating instance {instance_id}...")
        backend.terminate(instance_id)
        _update_state(run_id, status="terminated", finished_at=datetime.now(timezone.utc).isoformat())
        # Clean project-local state
        pr = project_root or state.get("project_root")
        if pr:
            project_state.remove_run(pr, run_id)
        print(f"[{run_id}] Instance terminated.")
    else:
        print(f"[{run_id}] No instance to terminate.")


def list_runs() -> list[dict]:
    """List all tracked cloud runs, most recent first."""
    runs = []
    for p in sorted(_state_dir().glob("cloud-*.json"), reverse=True):
        try:
            runs.append(json.loads(p.read_text()))
        except Exception:
            pass
    return runs


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _poll_and_retrieve(
    run_id: str,
    backend: ComputeBackend,
    instance_id: str,
    local_results_dir: Optional[str],
    sync_back: str,
) -> dict:
    """Poll S3 for exit_code, then retrieve results and terminate."""
    print(f"[{run_id}] Polling for completion (every {POLL_INTERVAL_SECONDS}s)...")
    start = time.monotonic()

    while True:
        exit_code = s3_helper.check_exit_code(run_id)
        if exit_code is not None:
            break

        # Also check if the instance died unexpectedly
        try:
            inst_status = backend.status(instance_id)
            if inst_status in ("terminated", "stopped", "shutting-down"):
                # Instance gone but no exit code — check one more time
                time.sleep(5)
                exit_code = s3_helper.check_exit_code(run_id)
                if exit_code is None:
                    exit_code = 1  # Assume failure
                break
        except Exception:
            pass

        elapsed = time.monotonic() - start
        hours = elapsed / 3600
        print(f"  ... {elapsed/60:.0f}m elapsed, instance: {inst_status if 'inst_status' in dir() else 'unknown'}")
        time.sleep(POLL_INTERVAL_SECONDS)

    elapsed = time.monotonic() - start
    status = "completed" if exit_code == 0 else "failed"
    print(f"[{run_id}] Finished in {elapsed/60:.1f}m with exit code {exit_code}")

    _update_state(
        run_id,
        exit_code=exit_code,
        status=status,
        finished_at=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=round(elapsed),
    )

    # Retrieve results
    if local_results_dir is None:
        local_results_dir = f"cloud-results/{run_id}"
    pull_results(run_id, local_results_dir)

    # Terminate instance (it may have already self-terminated via shutdown)
    try:
        backend.terminate(instance_id)
    except Exception:
        pass

    # Clean project-local state
    final = _load_state(run_id)
    pr = final.get("project_root")
    if pr:
        project_state.remove_run(pr, run_id)

    return final
