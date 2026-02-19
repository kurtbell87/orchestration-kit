"""Cloud compute configuration: instance catalog, pricing, decision thresholds."""

import os
import sys

S3_BUCKET = "kenoma-labs-research"
AWS_REGION = "us-east-1"
SSH_KEY_NAME = "kenoma-research"
SSH_KEY_PATH = "~/.ssh/kenoma-research.pem"

# Local machine thresholds — below these, run locally
LOCAL_MAX_WALL_HOURS = 2.0
LOCAL_MAX_MEMORY_GB = 16

# ---------------------------------------------------------------------------
# Cloud preference — three-tier mechanism
# ---------------------------------------------------------------------------
# "local"        — current behavior, cloud only when local can't handle it
# "cloud-first"  — prefer cloud, local only for trivially small jobs
# "cloud-always" — cloud for everything above overhead floor
_VALID_CLOUD_PREFERENCES = ("local", "cloud-first", "cloud-always")

_raw_pref = os.environ.get("ORCHESTRATION_KIT_CLOUD_PREFERENCE", "local").strip()
if _raw_pref not in _VALID_CLOUD_PREFERENCES:
    print(
        f"WARNING: ORCHESTRATION_KIT_CLOUD_PREFERENCE='{_raw_pref}' is invalid. "
        f"Valid values: {_VALID_CLOUD_PREFERENCES}. Falling back to 'local'.",
        file=sys.stderr,
    )
    _raw_pref = "local"

CLOUD_PREFERENCE: str = _raw_pref

# Jobs under this wall-time stay local even under cloud-always,
# because 3-5 min provisioning overhead would make cloud slower net-net.
CLOUD_OVERHEAD_FLOOR_HOURS = 0.15  # ~10 minutes

# Cost guardrails
DEFAULT_MAX_HOURS = 12
SPOT_MAX_WALL_HOURS = 4.0  # Above this, default to on-demand (spot interruption risk)

# S3 paths
S3_RUNS_PREFIX = "cloud-runs"

# State directory for detached runs
STATE_DIR = "~/.orchestration-kit-cloud/runs"

# Polling
POLL_INTERVAL_SECONDS = 30

# Tags applied to all cloud resources for identification and cleanup
RESOURCE_TAGS = {
    "Project": "orchestration-kit",
    "ManagedBy": "cloud-run",
}

# ---------------------------------------------------------------------------
# EC2 instance catalog
# ---------------------------------------------------------------------------
EC2_INSTANCES = {
    "c7a.4xlarge": {
        "vcpus": 16,
        "memory_gb": 32,
        "cost_ondemand": 0.77,
        "cost_spot": 0.24,
    },
    "c7a.8xlarge": {
        "vcpus": 32,
        "memory_gb": 64,
        "cost_ondemand": 1.55,
        "cost_spot": 0.47,
    },
    "c7a.16xlarge": {
        "vcpus": 64,
        "memory_gb": 128,
        "cost_ondemand": 3.10,
        "cost_spot": 0.94,
    },
}

# ---------------------------------------------------------------------------
# RunPod GPU catalog
# ---------------------------------------------------------------------------
RUNPOD_GPUS = {
    "A100": {
        "gpu_id": "NVIDIA A100 80GB PCIe",
        "vram_gb": 80,
        "cost_per_hour": 1.19,
    },
    "H100": {
        "gpu_id": "NVIDIA H100 80GB HBM3",
        "vram_gb": 80,
        "cost_per_hour": 2.69,
    },
}

RUNPOD_DEFAULT_IMAGE = "runpod/pytorch:2.1.0-py3.11-cuda11.8.0-devel-ubuntu22.04"
RUNPOD_DEFAULT_DATACENTER = "US-NC-1"
RUNPOD_S3_ENDPOINT_TEMPLATE = "https://s3api-{dc}.runpod.io/"

# Network volume threshold — use network volume for data > this size
RUNPOD_NETWORK_VOLUME_THRESHOLD_GB = 5

# ---------------------------------------------------------------------------
# EC2 instance selection rules
# ---------------------------------------------------------------------------

def select_ec2_instance(sequential_fits: int, estimated_rows: int) -> str:
    """Pick EC2 instance type based on workload characteristics."""
    if sequential_fits <= 20:
        return "c7a.4xlarge"
    if sequential_fits <= 200 and estimated_rows < 5_000_000:
        return "c7a.8xlarge"
    return "c7a.16xlarge"


def select_runpod_gpu(gpu_type: str) -> dict:
    """Pick RunPod GPU config. Returns catalog entry."""
    key = gpu_type.upper() if gpu_type and gpu_type.lower() not in ("any", "none") else "A100"
    return RUNPOD_GPUS.get(key, RUNPOD_GPUS["A100"])


def should_use_spot(estimated_wall_hours: float) -> bool:
    """Spot for short runs, on-demand for long ones (interruption risk)."""
    return estimated_wall_hours <= SPOT_MAX_WALL_HOURS
