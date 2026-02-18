"""Pre-flight check: given a compute profile, recommend local vs cloud execution."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Optional

from .config import (
    LOCAL_MAX_MEMORY_GB,
    LOCAL_MAX_WALL_HOURS,
    EC2_INSTANCES,
    RUNPOD_GPUS,
    select_ec2_instance,
    select_runpod_gpu,
    should_use_spot,
)
from .spec_parser import ComputeProfile, parse_spec


def check(profile: ComputeProfile) -> dict:
    """Evaluate a compute profile and return a recommendation.

    Returns a dict with:
        recommendation: "local" | "remote"
        backend: "aws" | "runpod" | null
        instance_type: str | null
        vcpus / memory_gb / cost info
        reason: human-readable explanation
    """
    # GPU workloads → RunPod
    if profile.compute_type == "gpu" or (
        profile.gpu_type and profile.gpu_type not in ("none", "")
    ):
        gpu_info = select_runpod_gpu(profile.gpu_type)
        est_cost = gpu_info["cost_per_hour"] * max(profile.estimated_wall_hours, 0.5)
        return {
            "recommendation": "remote",
            "backend": "runpod",
            "instance_type": gpu_info["gpu_id"],
            "vcpus": None,
            "memory_gb": None,
            "vram_gb": gpu_info["vram_gb"],
            "cost_per_hour": gpu_info["cost_per_hour"],
            "estimated_total_cost": f"${est_cost:.2f}",
            "use_spot": False,
            "reason": _gpu_reason(profile, gpu_info),
        }

    # CPU workloads — check if local is sufficient
    if _can_run_locally(profile):
        return {
            "recommendation": "local",
            "backend": None,
            "instance_type": None,
            "vcpus": None,
            "memory_gb": None,
            "vram_gb": None,
            "cost_per_hour": 0,
            "estimated_total_cost": "$0.00",
            "reason": _local_reason(profile),
        }

    # CPU workload that exceeds local thresholds → AWS EC2
    instance_type = select_ec2_instance(
        profile.sequential_fits, profile.estimated_rows
    )
    inst = EC2_INSTANCES[instance_type]
    use_spot = should_use_spot(profile.estimated_wall_hours)
    rate = inst["cost_spot"] if use_spot else inst["cost_ondemand"]
    hours = max(profile.estimated_wall_hours, 0.5)
    est_cost_spot = inst["cost_spot"] * hours
    est_cost_od = inst["cost_ondemand"] * hours

    return {
        "recommendation": "remote",
        "backend": "aws",
        "instance_type": instance_type,
        "vcpus": inst["vcpus"],
        "memory_gb": inst["memory_gb"],
        "vram_gb": None,
        "cost_per_hour_spot": inst["cost_spot"],
        "cost_per_hour_ondemand": inst["cost_ondemand"],
        "estimated_total_cost": f"${est_cost_spot:.2f} (spot) – ${est_cost_od:.2f} (on-demand)",
        "use_spot": use_spot,
        "reason": _ec2_reason(profile, instance_type, inst),
    }


def check_spec(spec_path: str) -> dict:
    """Convenience: parse spec file and run pre-flight check."""
    profile = parse_spec(spec_path)
    result = check(profile)
    result["spec_file"] = str(spec_path)
    result["profile"] = asdict(profile)
    return result


# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------

def _can_run_locally(profile: ComputeProfile) -> bool:
    """True if the workload fits within local machine thresholds."""
    if profile.tier.lower() == "heavy":
        return False
    if profile.estimated_wall_hours > LOCAL_MAX_WALL_HOURS:
        return False
    if profile.memory_gb > LOCAL_MAX_MEMORY_GB:
        return False
    return True


# ---------------------------------------------------------------------------
# Reason strings
# ---------------------------------------------------------------------------

def _local_reason(profile: ComputeProfile) -> str:
    parts = []
    if profile.tier:
        parts.append(f"{profile.tier} tier")
    if profile.estimated_wall_hours:
        parts.append(f"est. {profile.estimated_wall_hours}h wall time")
    if profile.memory_gb:
        parts.append(f"{profile.memory_gb} GB memory")
    detail = ", ".join(parts) if parts else "workload within local thresholds"
    return f"Local execution OK: {detail}"


def _ec2_reason(profile: ComputeProfile, instance_type: str, inst: dict) -> str:
    parts = []
    if profile.tier:
        parts.append(f"{profile.tier} tier")
    if profile.sequential_fits:
        parts.append(f"{profile.sequential_fits} sequential fits")
    if profile.estimated_rows:
        parts.append(f"{profile.estimated_rows:,} rows")
    if profile.estimated_wall_hours:
        parts.append(f"est. {profile.estimated_wall_hours}h wall time")
    detail = ", ".join(parts) if parts else "exceeds local thresholds"
    return (
        f"Exceeds local limits: {detail}. "
        f"Recommended: {instance_type} ({inst['vcpus']} vCPU, {inst['memory_gb']} GB)"
    )


def _gpu_reason(profile: ComputeProfile, gpu_info: dict) -> str:
    parts = []
    if profile.model_type and profile.model_type != "other":
        parts.append(f"{profile.model_type} workload")
    if profile.estimated_wall_hours:
        parts.append(f"est. {profile.estimated_wall_hours}h")
    detail = ", ".join(parts) if parts else "GPU required"
    return f"GPU workload: {detail}. Recommended: {gpu_info['gpu_id']}"


# ---------------------------------------------------------------------------
# CLI entry point (called by tools/preflight)
# ---------------------------------------------------------------------------

def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Pre-flight compute check for experiment specs"
    )
    parser.add_argument("spec_file", help="Path to experiment spec (.md)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = check_spec(args.spec_file)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        rec = result["recommendation"]
        if rec == "local":
            print(f"RECOMMENDATION: Run locally")
            print(f"  Reason: {result['reason']}")
        else:
            backend = result["backend"].upper()
            inst = result["instance_type"]
            cost = result["estimated_total_cost"]
            print(f"RECOMMENDATION: Run on {backend}")
            print(f"  Instance: {inst}")
            if result.get("vcpus"):
                print(f"  Resources: {result['vcpus']} vCPU, {result['memory_gb']} GB RAM")
            if result.get("vram_gb"):
                print(f"  VRAM: {result['vram_gb']} GB")
            print(f"  Est. cost: {cost}")
            if result.get("use_spot") is False and result["backend"] == "aws":
                print(f"  Pricing: on-demand (est. wall time > 4h, spot interruption risk)")
            elif result.get("use_spot"):
                print(f"  Pricing: spot (with on-demand fallback)")
            print(f"  Reason: {result['reason']}")
            print()
            spec = result["spec_file"]
            print(f"  Command: tools/cloud-run \"<your-command>\" --spec {spec} --data-dirs <data-dir>")


if __name__ == "__main__":
    main()
