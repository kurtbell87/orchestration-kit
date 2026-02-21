"""Abstract base class for cloud compute backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InstanceConfig:
    """Configuration for provisioning a cloud instance."""

    instance_type: str                      # e.g., "c7a.8xlarge" or "NVIDIA A100 80GB PCIe"
    run_id: str                             # Unique run identifier
    s3_prefix: str                          # S3 URI prefix for this run
    command: str                            # Command to execute remotely
    max_hours: float = 12.0                 # Hard deadline — auto-terminate after this
    use_spot: bool = True                   # EC2: spot vs on-demand
    env_vars: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)

    # RunPod-specific
    docker_image: Optional[str] = None
    gpu_type: Optional[str] = None
    network_volume_id: Optional[str] = None

    # EC2 Docker/ECR execution
    image_uri: Optional[str] = None              # ECR image URI (e.g., 123456.dkr.ecr.us-east-1.amazonaws.com/mbo-dl:abc123)
    ebs_snapshot_id: Optional[str] = None         # EBS snapshot with pre-loaded data
    iam_instance_profile: Optional[str] = None    # IAM instance profile name for ECR pull + S3 access

    # Set by backend after launch
    launched_at: Optional[str] = None       # ISO 8601 UTC timestamp


class ComputeBackend(ABC):
    """Interface for cloud compute backends (EC2, RunPod)."""

    @abstractmethod
    def provision(self, config: InstanceConfig) -> str:
        """Launch a cloud instance/pod.

        Returns an instance_id (EC2 instance ID or RunPod pod ID).
        """

    @abstractmethod
    def wait_ready(self, instance_id: str, timeout: int = 600) -> None:
        """Block until the instance is ready to execute.

        Raises TimeoutError if not ready within timeout seconds.
        """

    @abstractmethod
    def status(self, instance_id: str) -> str:
        """Return instance status string (e.g., 'running', 'terminated')."""

    @abstractmethod
    def terminate(self, instance_id: str) -> None:
        """Terminate the instance and clean up associated resources."""

    @abstractmethod
    def cleanup_resources(self, run_id: str) -> list[str]:
        """Clean up orphaned resources (security groups, volumes, etc.) for a run.

        Returns list of cleaned-up resource descriptions.
        """

    def find_instances_by_spec(self, spec: str) -> list[dict]:
        """Find running instances launched for a given spec file.

        Returns list of dicts with instance_id, run_id, launched_at, state.
        Default implementation returns [] (no spec-level tracking).
        """
        return []
