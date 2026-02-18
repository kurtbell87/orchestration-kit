"""RunPod GPU compute backend — pod lifecycle via RunPod SDK, data via S3."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from ..config import (
    AWS_REGION,
    RESOURCE_TAGS,
    RUNPOD_DEFAULT_DATACENTER,
    RUNPOD_DEFAULT_IMAGE,
    RUNPOD_NETWORK_VOLUME_THRESHOLD_GB,
    RUNPOD_S3_ENDPOINT_TEMPLATE,
    S3_BUCKET,
    S3_RUNS_PREFIX,
)
from .base import ComputeBackend, InstanceConfig

# Bootstrap script template path
BOOTSTRAP_SCRIPT = Path(__file__).parent.parent / "scripts" / "runpod-bootstrap.sh"

# REST API base
RUNPOD_REST_BASE = "https://rest.runpod.io/v1"


class RunPodBackend(ComputeBackend):
    """RunPod pod lifecycle management with S3-based data transfer."""

    def __init__(self):
        try:
            import runpod
        except ImportError:
            raise ImportError("runpod SDK is required: pip install runpod")
        self._runpod = runpod

        # Ensure API key is set (runpodctl stores it as lowercase "apikey")
        api_key = os.environ.get("RUNPOD_API_KEY", "")
        if not api_key:
            config_path = Path.home() / ".runpod" / "config.toml"
            if config_path.exists():
                for line in config_path.read_text().splitlines():
                    line_s = line.strip()
                    if line_s.startswith("apikey") or line_s.startswith("apiKey"):
                        api_key = line_s.split("=", 1)[1].strip().strip('"').strip("'")
                        break

        if api_key:
            os.environ["RUNPOD_API_KEY"] = api_key
            runpod.api_key = api_key

        self._api_key = api_key

    # -------------------------------------------------------------------
    # Pod lifecycle
    # -------------------------------------------------------------------

    def provision(self, config: InstanceConfig) -> str:
        """Create a RunPod pod with the experiment command. Returns pod ID."""
        docker_image = config.docker_image or RUNPOD_DEFAULT_IMAGE
        gpu_type = config.gpu_type or config.instance_type or "NVIDIA GeForce RTX 4090"

        bootstrap_raw = BOOTSTRAP_SCRIPT.read_text()

        env = {
            "RUN_ID": config.run_id,
            "S3_BUCKET": S3_BUCKET,
            "S3_PREFIX": f"{S3_RUNS_PREFIX}/{config.run_id}",
            "AWS_DEFAULT_REGION": AWS_REGION,
            "EXPERIMENT_COMMAND": config.command,
            "MAX_HOURS": str(config.max_hours),
            "RUNPOD_API_KEY": self._api_key,
        }
        env.update(config.env_vars)

        # Forward AWS credentials so the pod can access S3 for code/results.
        import boto3 as _boto3
        _session = _boto3.Session()
        _creds = _session.get_credentials()
        if _creds:
            _frozen = _creds.get_frozen_credentials()
            env.setdefault("AWS_ACCESS_KEY_ID", _frozen.access_key)
            env.setdefault("AWS_SECRET_ACCESS_KEY", _frozen.secret_key)
            if _frozen.token:
                env.setdefault("AWS_SESSION_TOKEN", _frozen.token)

        # Render the bootstrap with env vars injected, then base64 encode.
        # This avoids GraphQL string escaping issues with the full script.
        var_block = "\n".join(f'export {k}="{v}"' for k, v in env.items())
        lines = bootstrap_raw.split("\n", 1)
        if lines[0].startswith("#!"):
            full_script = lines[0] + "\n" + var_block + "\n" + lines[1]
        else:
            full_script = var_block + "\n" + bootstrap_raw
        b64 = base64.b64encode(full_script.encode()).decode()
        docker_args = f"bash -c 'echo {b64} | base64 -d | bash'"

        # When a network volume is attached, it replaces local persistent storage.
        use_network_vol = bool(config.network_volume_id)

        # Suppress RunPod SDK's debug print that leaks env vars (including creds).
        import io, contextlib
        _captured = io.StringIO()
        with contextlib.redirect_stdout(_captured):
            pod = self._runpod.create_pod(
                name=f"okit-{config.run_id[:24]}",
                image_name=docker_image,
                gpu_type_id=gpu_type,
                gpu_count=1,
                volume_in_gb=0 if use_network_vol else 50,
                container_disk_in_gb=20,
                volume_mount_path="/workspace",
                env=env,
                docker_args=docker_args,
                network_volume_id=config.network_volume_id,
            )

        return pod["id"]

    def wait_ready(self, instance_id: str, timeout: int = 600) -> None:
        """Wait until the RunPod pod is running."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            pod = self._runpod.get_pod(instance_id)
            desired = pod.get("desiredStatus", "")
            runtime = pod.get("runtime", {})
            if runtime and runtime.get("uptimeInSeconds", 0) > 0:
                return
            if desired == "EXITED":
                return  # Pod finished quickly — proceed to poll S3
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
            pass

    def cleanup_resources(self, run_id: str) -> list[str]:
        return []

    def gc(self) -> list[str]:
        """Garbage-collect orphaned RunPod pods."""
        cleaned = []
        try:
            pods = self._runpod.get_pods()
            for pod in pods:
                name = pod.get("name", "")
                if name.startswith("okit-"):
                    if pod.get("desiredStatus") == "EXITED":
                        try:
                            self._runpod.terminate_pod(pod["id"])
                            cleaned.append(f"Pod {pod['id']} ({name}, EXITED)")
                        except Exception:
                            pass
        except Exception:
            pass
        return cleaned

    # -------------------------------------------------------------------
    # Network volume lifecycle
    # -------------------------------------------------------------------

    def create_network_volume(
        self, name: str, size_gb: int = 10, datacenter: str = RUNPOD_DEFAULT_DATACENTER,
    ) -> str:
        """Create a RunPod network volume. Returns volume ID."""
        data = json.dumps({
            "name": name,
            "size": size_gb,
            "dataCenterId": datacenter,
        }).encode()
        req = urllib.request.Request(
            f"{RUNPOD_REST_BASE}/networkvolumes",
            data=data,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
        return result["id"]

    def destroy_volume(self, volume_id: str) -> None:
        """Destroy a RunPod network volume."""
        req = urllib.request.Request(
            f"{RUNPOD_REST_BASE}/networkvolumes/{volume_id}",
            method="DELETE",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            if e.code != 204:
                raise

    def list_volumes(self) -> list[dict]:
        """List all network volumes."""
        req = urllib.request.Request(
            f"{RUNPOD_REST_BASE}/networkvolumes",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def sync_to_volume(
        self,
        volume_id: str,
        source: str,
        datacenter: str = RUNPOD_DEFAULT_DATACENTER,
    ) -> None:
        """Upload a file or directory to a network volume via RunPod S3 API."""
        client = self._runpod_s3_client(datacenter)
        src = Path(source)
        if src.is_file():
            client.upload_file(str(src), volume_id, src.name)
        elif src.is_dir():
            for f in src.rglob("*"):
                if f.is_file():
                    key = str(f.relative_to(src))
                    client.upload_file(str(f), volume_id, key)
        else:
            raise FileNotFoundError(f"Source not found: {source}")

    def download_from_volume(
        self,
        volume_id: str,
        key: str,
        local_path: str,
        datacenter: str = RUNPOD_DEFAULT_DATACENTER,
    ) -> None:
        """Download a file from a network volume via RunPod S3 API."""
        client = self._runpod_s3_client(datacenter)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        client.download_file(volume_id, key, local_path)

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _runpod_s3_client(self, datacenter: str = RUNPOD_DEFAULT_DATACENTER):
        """Create a boto3 S3 client for the RunPod S3-compatible endpoint."""
        import boto3

        access_key = os.environ.get("RUNPOD_S3_ACCESS_KEY", "")
        secret_key = os.environ.get("RUNPOD_S3_SECRET", "")
        if not access_key or not secret_key:
            raise RuntimeError(
                "RUNPOD_S3_ACCESS_KEY and RUNPOD_S3_SECRET must be set. "
                "Generate them at RunPod Console > Settings > S3 API Keys."
            )

        endpoint = RUNPOD_S3_ENDPOINT_TEMPLATE.format(dc=datacenter.lower())
        return boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=datacenter,
            endpoint_url=endpoint,
        )
