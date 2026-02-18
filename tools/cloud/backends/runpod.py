"""RunPod GPU compute backend — pod lifecycle via RunPod SDK, data via S3."""

from __future__ import annotations

import os
import time
from typing import Optional

from ..config import (
    AWS_REGION,
    RESOURCE_TAGS,
    RUNPOD_DEFAULT_IMAGE,
    RUNPOD_NETWORK_VOLUME_THRESHOLD_GB,
    S3_BUCKET,
    S3_RUNS_PREFIX,
)
from .base import ComputeBackend, InstanceConfig

# Bootstrap script template path
from pathlib import Path
BOOTSTRAP_SCRIPT = Path(__file__).parent.parent / "scripts" / "runpod-bootstrap.sh"


class RunPodBackend(ComputeBackend):
    """RunPod pod lifecycle management with S3-based data transfer."""

    def __init__(self):
        try:
            import runpod
        except ImportError:
            raise ImportError("runpod SDK is required: pip install runpod")
        self._runpod = runpod

        # Ensure API key is set (runpodctl may have set it in config but SDK needs env var)
        if not os.environ.get("RUNPOD_API_KEY"):
            # Try to read from runpodctl config
            config_path = Path.home() / ".runpod" / "config.toml"
            if config_path.exists():
                for line in config_path.read_text().splitlines():
                    if line.strip().startswith("apiKey"):
                        key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        os.environ["RUNPOD_API_KEY"] = key
                        break

    def provision(self, config: InstanceConfig) -> str:
        """Create a RunPod pod with the experiment command.

        Returns the pod ID.
        """
        docker_image = config.docker_image or RUNPOD_DEFAULT_IMAGE
        gpu_type = config.gpu_type or "NVIDIA A100 80GB PCIe"

        # Build the startup command
        bootstrap = BOOTSTRAP_SCRIPT.read_text()

        # Environment variables for the pod
        env = {
            "RUN_ID": config.run_id,
            "S3_BUCKET": S3_BUCKET,
            "S3_PREFIX": f"{S3_RUNS_PREFIX}/{config.run_id}",
            "AWS_DEFAULT_REGION": AWS_REGION,
            "EXPERIMENT_COMMAND": config.command,
            "MAX_HOURS": str(config.max_hours),
        }
        env.update(config.env_vars)

        # Pass AWS credentials for S3 access
        for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            val = os.environ.get(key)
            if val:
                env[key] = val

        pod = self._runpod.create_pod(
            name=f"okit-{config.run_id[:24]}",
            image_name=docker_image,
            gpu_type_id=gpu_type,
            gpu_count=1,
            volume_in_gb=50,
            container_disk_in_gb=20,
            env=env,
            docker_args=f"bash -c '{bootstrap}'",
            # Network volume if configured
            network_volume_id=config.network_volume_id,
        )

        pod_id = pod["id"]
        return pod_id

    def wait_ready(self, instance_id: str, timeout: int = 600) -> None:
        """Wait until the RunPod pod is running."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            pod = self._runpod.get_pod(instance_id)
            status = pod.get("desiredStatus", "")
            runtime = pod.get("runtime", {})
            if runtime and runtime.get("uptimeInSeconds", 0) > 0:
                return
            if status == "EXITED":
                raise RuntimeError(f"Pod {instance_id} exited before becoming ready")
            time.sleep(10)
        raise TimeoutError(f"Pod {instance_id} not ready after {timeout}s")

    def status(self, instance_id: str) -> str:
        """Return pod status."""
        try:
            pod = self._runpod.get_pod(instance_id)
            desired = pod.get("desiredStatus", "UNKNOWN")
            runtime = pod.get("runtime", {})
            if desired == "EXITED" or (runtime and runtime.get("uptimeInSeconds") == 0 and desired == "RUNNING"):
                return "terminated"
            return desired.lower()
        except Exception:
            return "unknown"

    def terminate(self, instance_id: str) -> None:
        """Terminate the RunPod pod."""
        try:
            self._runpod.terminate_pod(instance_id)
        except Exception:
            pass  # Pod may have already terminated

    def cleanup_resources(self, run_id: str) -> list[str]:
        """Clean up resources for a specific run."""
        cleaned = []
        # RunPod pods self-terminate; nothing persistent to clean up
        # (network volumes are reusable and intentionally kept)
        return cleaned

    def gc(self) -> list[str]:
        """Garbage-collect orphaned RunPod pods."""
        cleaned = []
        try:
            pods = self._runpod.get_pods()
            for pod in pods:
                name = pod.get("name", "")
                if name.startswith("okit-"):
                    desired = pod.get("desiredStatus", "")
                    if desired == "EXITED":
                        try:
                            self._runpod.terminate_pod(pod["id"])
                            cleaned.append(f"Pod {pod['id']} ({name}, EXITED)")
                        except Exception:
                            pass
        except Exception:
            pass
        return cleaned

    # -----------------------------------------------------------------------
    # Network volume helpers
    # -----------------------------------------------------------------------

    def create_network_volume(self, name: str, size_gb: int = 100, region: str = "US-TX-3") -> str:
        """Create a RunPod network volume. Returns volume ID."""
        # RunPod SDK may not have this — fall back to API call
        import requests
        api_key = os.environ.get("RUNPOD_API_KEY", "")
        resp = requests.post(
            "https://api.runpod.io/graphql",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "query": """
                    mutation createNetworkVolume($input: CreateNetworkVolumeInput!) {
                        createNetworkVolume(input: $input) { id name }
                    }
                """,
                "variables": {
                    "input": {
                        "name": name,
                        "size": size_gb,
                        "dataCenterId": region,
                    }
                },
            },
        )
        data = resp.json()
        return data["data"]["createNetworkVolume"]["id"]
