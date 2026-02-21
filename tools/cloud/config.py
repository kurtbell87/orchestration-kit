"""Cloud compute configuration: instance catalog, pricing, decision thresholds."""

import os
import sys

S3_BUCKET = "kenoma-labs-research"
AWS_REGION = "us-east-1"
SSH_KEY_NAME = "kenoma-research"
SSH_KEY_PATH = "~/.ssh/kenoma-research.pem"

# ---------------------------------------------------------------------------
# Runtime configuration — multi-runtime Docker support
# ---------------------------------------------------------------------------
VALID_RUNTIMES = ("python", "cpp", "cpp-python")
DEFAULT_RUNTIME = "python"

ECR_REGISTRY = os.environ.get("ORCHESTRATION_KIT_ECR_REGISTRY", "")
DOCKERHUB_REGISTRY = os.environ.get("ORCHESTRATION_KIT_DOCKERHUB_REGISTRY", "kenoma-labs")
DOCKER_REPO_NAME = "orchestration-kit"

# EC2 Docker images per runtime (ECR for custom images, public for python)
EC2_RUNTIME_IMAGES = {
    "python": "python:3.12-slim",
    "cpp": f"{ECR_REGISTRY}/{DOCKER_REPO_NAME}:cpp-latest" if ECR_REGISTRY else "ubuntu:22.04",
    "cpp-python": f"{ECR_REGISTRY}/{DOCKER_REPO_NAME}:cpp-python-latest" if ECR_REGISTRY else "ubuntu:22.04",
}

# RunPod Docker images per runtime (DockerHub for custom, default for python)
RUNPOD_RUNTIME_IMAGES = {
    "python": "runpod/pytorch:2.1.0-py3.11-cuda11.8.0-devel-ubuntu22.04",
    "cpp": f"{DOCKERHUB_REGISTRY}/{DOCKER_REPO_NAME}:cpp-latest",
    "cpp-python": f"{DOCKERHUB_REGISTRY}/{DOCKER_REPO_NAME}:cpp-python-latest",
}

# Default EC2 instances for C++ runtimes (need compilation headroom)
RUNTIME_EC2_DEFAULTS = {
    "python": "c7a.4xlarge",
    "cpp": "c7a.8xlarge",
    "cpp-python": "c7a.8xlarge",
}

# RunPod cloud type — secure only (community cloud disabled)
RUNPOD_CLOUD_TYPE = "SECURE"

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

# ---------------------------------------------------------------------------
# ECR / EBS / IAM — Docker-based execution (env-var overridable)
# ---------------------------------------------------------------------------
ECR_REPO_URI = os.environ.get("CLOUD_RUN_ECR_REPO_URI", "")
EBS_DATA_SNAPSHOT_ID = os.environ.get("CLOUD_RUN_EBS_SNAPSHOT_ID", "")
EBS_DATA_VOLUME_SIZE_GB = int(os.environ.get("CLOUD_RUN_EBS_VOLUME_SIZE_GB", "60"))
EBS_DATA_DEVICE_NAME = os.environ.get("CLOUD_RUN_EBS_DEVICE_NAME", "/dev/xvdf")
IAM_INSTANCE_PROFILE = os.environ.get("CLOUD_RUN_IAM_PROFILE", "")

# ECS-optimized Amazon Linux 2023 AMI (Docker pre-installed)
ECS_OPTIMIZED_AMI_SSM_PARAM = os.environ.get(
    "CLOUD_RUN_ECS_AMI_SSM_PARAM",
    "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id",
)

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
    "c7a.32xlarge": {
        "vcpus": 128,
        "memory_gb": 256,
        "cost_ondemand": 6.20,
        "cost_spot": 1.88,
    },
}

# ---------------------------------------------------------------------------
# RunPod GPU catalog
# ---------------------------------------------------------------------------
RUNPOD_GPUS = {
    "L40S": {
        "gpu_id": "NVIDIA L40S",
        "vram_gb": 48,
        "cost_per_hour_spot": 0.26,
        "cost_per_hour": 0.86,
    },
    "RTX4090": {
        "gpu_id": "NVIDIA GeForce RTX 4090",
        "vram_gb": 24,
        "cost_per_hour_spot": 0.29,
        "cost_per_hour": 0.59,
    },
    "A100": {
        "gpu_id": "NVIDIA A100 80GB PCIe",
        "vram_gb": 80,
        "cost_per_hour_spot": 0.82,
        "cost_per_hour": 1.39,
    },
    "H100": {
        "gpu_id": "NVIDIA H100 PCIe",
        "vram_gb": 80,
        "cost_per_hour_spot": 1.25,
        "cost_per_hour": 2.39,
    },
    "H200": {
        "gpu_id": "NVIDIA H200",
        "vram_gb": 141,
        "cost_per_hour_spot": 2.29,
        "cost_per_hour": 3.59,
    },
}

RUNPOD_DEFAULT_GPU = "NVIDIA L40S"
RUNPOD_DEFAULT_IMAGE = "runpod/pytorch:2.1.0-py3.11-cuda11.8.0-devel-ubuntu22.04"
RUNPOD_DEFAULT_DATACENTER = "US-NC-1"
RUNPOD_S3_ENDPOINT_TEMPLATE = "https://s3api-{dc}.runpod.io/"

# Network volume threshold — use network volume for data > this size
RUNPOD_NETWORK_VOLUME_THRESHOLD_GB = 5

# ---------------------------------------------------------------------------
# EC2 instance selection rules
# ---------------------------------------------------------------------------

def select_ec2_instance(sequential_fits: int, estimated_rows: int, runtime: str = "python") -> str:
    """Pick EC2 instance type based on workload characteristics and runtime."""
    # C++ runtimes with no model fitting (data export) get compilation-friendly default
    if runtime in ("cpp", "cpp-python") and sequential_fits == 0:
        return RUNTIME_EC2_DEFAULTS.get(runtime, "c7a.8xlarge")
    if sequential_fits <= 20:
        return "c7a.4xlarge"
    if sequential_fits <= 200 and estimated_rows < 5_000_000:
        return "c7a.8xlarge"
    return "c7a.16xlarge"


def select_runpod_gpu(gpu_type: str) -> dict:
    """Pick RunPod GPU config. Returns catalog entry."""
    key = gpu_type.upper() if gpu_type and gpu_type.lower() not in ("any", "none") else "L40S"
    return RUNPOD_GPUS.get(key, RUNPOD_GPUS["L40S"])


def should_use_spot(estimated_wall_hours: float) -> bool:
    """Spot for short runs, on-demand for long ones (interruption risk)."""
    return estimated_wall_hours <= SPOT_MAX_WALL_HOURS
