"""Instance reaper — terminates cloud instances that have exceeded their lease."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .backends.base import ComputeBackend
from . import state as project_state


DEFAULT_HARD_CEILING_HOURS = 24


def reap(
    backend: ComputeBackend,
    *,
    dry_run: bool = False,
    hard_ceiling_hours: float = DEFAULT_HARD_CEILING_HOURS,
    project_root: Optional[str] = None,
) -> list[dict]:
    """Find and terminate instances that have exceeded their lease or hard ceiling.

    Returns a list of action summaries:
        {instance_id, run_id, age_hours, max_hours, reason, action}
    """
    now = datetime.now(timezone.utc)

    # Use paginated describe_instances filtered on cloud-run:launched-at tag presence
    try:
        ec2 = backend._ec2  # noqa: SLF001 — we need the raw client for pagination
    except AttributeError:
        # Non-AWS backend: nothing to reap
        return []

    paginator = ec2.get_paginator("describe_instances")
    pages = paginator.paginate(
        Filters=[
            {"Name": "tag-key", "Values": ["cloud-run:launched-at"]},
            {"Name": "instance-state-name", "Values": ["running", "pending"]},
        ]
    )

    actions = []
    for page in pages:
        for res in page["Reservations"]:
            for inst in res["Instances"]:
                tag_map = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                launched_at_str = tag_map.get("cloud-run:launched-at", "")
                if not launched_at_str:
                    continue

                try:
                    launched_at = datetime.fromisoformat(launched_at_str)
                except (ValueError, TypeError):
                    continue

                age_hours = (now - launched_at).total_seconds() / 3600
                max_hours_str = tag_map.get("cloud-run:max-hours", "")
                try:
                    max_hours = float(max_hours_str)
                except (ValueError, TypeError):
                    max_hours = None

                instance_id = inst["InstanceId"]
                run_id = tag_map.get("cloud-run:run-id", "")
                reason = None

                if max_hours is not None and age_hours > max_hours:
                    reason = f"lease_expired ({age_hours:.1f}h > {max_hours}h)"
                elif age_hours > hard_ceiling_hours:
                    reason = f"hard_ceiling ({age_hours:.1f}h > {hard_ceiling_hours}h)"

                if reason is None:
                    continue

                action = "would_terminate" if dry_run else "terminated"
                if not dry_run:
                    backend.terminate(instance_id)
                    if project_root and run_id:
                        project_state.remove_run(project_root, run_id)

                actions.append({
                    "instance_id": instance_id,
                    "run_id": run_id,
                    "age_hours": round(age_hours, 2),
                    "max_hours": max_hours,
                    "reason": reason,
                    "action": action,
                })

    return actions
