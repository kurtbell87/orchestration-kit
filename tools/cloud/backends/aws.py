"""AWS EC2 compute backend — Docker-based execution on compute-optimized instances."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

from ..config import (
    AWS_REGION,
    SSH_KEY_NAME,
    RESOURCE_TAGS,
    S3_BUCKET,
    S3_RUNS_PREFIX,
)
from .base import ComputeBackend, InstanceConfig

# Amazon Linux 2023 AMI lookup via SSM parameter
AL2023_SSM_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"

# Bootstrap script template path (relative to this file)
BOOTSTRAP_SCRIPT = Path(__file__).parent.parent / "scripts" / "ec2-bootstrap.sh"


class AWSBackend(ComputeBackend):
    """EC2 lifecycle management with Docker-based experiment execution."""

    def __init__(self):
        try:
            import boto3
        except ImportError:
            raise ImportError("boto3 is required for AWS backend: pip install boto3")
        self._ec2 = boto3.client("ec2", region_name=AWS_REGION)
        self._ec2_resource = boto3.resource("ec2", region_name=AWS_REGION)
        self._ssm = boto3.client("ssm", region_name=AWS_REGION)

    def provision(self, config: InstanceConfig) -> str:
        """Launch an EC2 instance with the bootstrap user-data script.

        Handles spot requests with on-demand fallback.
        Idempotent: returns existing instance if one is already running for this run_id.
        """
        # --- Idempotency: check for existing instance with same run_id ---
        existing = self._find_existing_instance(config.run_id)
        if existing:
            log.warning(
                "Instance %s already running for run %s — returning existing",
                existing, config.run_id,
            )
            return existing

        ami_id = self._get_latest_ami()
        sg_id = self._ensure_security_group(config.run_id)
        user_data = self._render_user_data(config)

        launched_at = datetime.now(timezone.utc).isoformat()
        config.launched_at = launched_at

        tags = {
            **RESOURCE_TAGS,
            "RunId": config.run_id,
            **config.tags,
            "cloud-run:run-id": config.run_id,
            "cloud-run:spec": (config.tags.get("SpecFile", "") or "")[:256],
            "cloud-run:max-hours": str(config.max_hours),
            "cloud-run:launched-at": launched_at,
        }
        tag_specs = [
            {
                "ResourceType": rt,
                "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
            }
            for rt in ["instance", "volume"]
        ]

        # EC2-native idempotency token (max 64 chars, 7-day dedup window)
        client_token = f"cloud-run-{config.run_id}"[:64]

        launch_kwargs = dict(
            ImageId=ami_id,
            InstanceType=config.instance_type,
            KeyName=SSH_KEY_NAME,
            SecurityGroupIds=[sg_id],
            UserData=user_data,
            MinCount=1,
            MaxCount=1,
            TagSpecifications=tag_specs,
            ClientToken=client_token,
            # Auto-terminate when the instance shuts itself down
            InstanceInitiatedShutdownBehavior="terminate",
            # IAM role for S3 access (if instance profile exists)
            # IamInstanceProfile={"Name": "..."},  # uncomment if IAM profile is set up
        )

        if config.use_spot:
            launch_kwargs["InstanceMarketOptions"] = {
                "MarketType": "spot",
                "SpotOptions": {
                    "SpotInstanceType": "one-time",
                    "InstanceInterruptionBehavior": "terminate",
                },
            }

        try:
            response = self._ec2.run_instances(**launch_kwargs)
        except self._ec2.exceptions.ClientError as e:
            # Spot capacity unavailable — retry with on-demand
            if config.use_spot and "InsufficientInstanceCapacity" in str(e):
                launch_kwargs.pop("InstanceMarketOptions", None)
                response = self._ec2.run_instances(**launch_kwargs)
            else:
                raise

        instance_id = response["Instances"][0]["InstanceId"]
        return instance_id

    def wait_ready(self, instance_id: str, timeout: int = 600) -> None:
        """Wait until the EC2 instance passes status checks.

        If the instance self-terminates before checks pass (fast experiments),
        this returns normally — the caller should proceed to poll S3 for results.
        """
        waiter = self._ec2.get_waiter("instance_status_ok")
        try:
            waiter.wait(
                InstanceIds=[instance_id],
                WaiterConfig={"Delay": 15, "MaxAttempts": timeout // 15},
            )
        except Exception:
            # Instance may have already terminated (fast experiment).
            state = self.status(instance_id)
            if state in ("terminated", "shutting-down", "stopped"):
                return  # Instance finished — proceed to result retrieval
            raise

    def status(self, instance_id: str) -> str:
        """Return EC2 instance state name."""
        resp = self._ec2.describe_instances(InstanceIds=[instance_id])
        return resp["Reservations"][0]["Instances"][0]["State"]["Name"]

    def terminate(self, instance_id: str) -> None:
        """Terminate EC2 instance and clean up its security group."""
        # Get the run_id tag before terminating
        run_id = None
        try:
            resp = self._ec2.describe_instances(InstanceIds=[instance_id])
            inst = resp["Reservations"][0]["Instances"][0]
            sg_ids = [sg["GroupId"] for sg in inst.get("SecurityGroups", [])]
            for tag in inst.get("Tags", []):
                if tag["Key"] == "RunId":
                    run_id = tag["Value"]
                    break
        except Exception:
            sg_ids = []

        self._ec2.terminate_instances(InstanceIds=[instance_id])

        # Wait for termination before cleaning up security group
        waiter = self._ec2.get_waiter("instance_terminated")
        try:
            waiter.wait(
                InstanceIds=[instance_id],
                WaiterConfig={"Delay": 10, "MaxAttempts": 30},
            )
        except Exception:
            pass

        # Clean up security groups tagged with our run
        for sg_id in sg_ids:
            self._try_delete_security_group(sg_id)

    def cleanup_resources(self, run_id: str) -> list[str]:
        """Clean up orphaned security groups and terminated instances for a run."""
        cleaned = []

        # Find security groups with our tags
        resp = self._ec2.describe_security_groups(
            Filters=[
                {"Name": "tag:ManagedBy", "Values": ["cloud-run"]},
                {"Name": "tag:RunId", "Values": [run_id]},
            ]
        )
        for sg in resp["SecurityGroups"]:
            if self._try_delete_security_group(sg["GroupId"]):
                cleaned.append(f"SecurityGroup {sg['GroupId']}")

        return cleaned

    def gc(self) -> list[str]:
        """Garbage-collect ALL orphaned resources managed by cloud-run."""
        cleaned = []

        # Find all security groups tagged as ours
        resp = self._ec2.describe_security_groups(
            Filters=[{"Name": "tag:ManagedBy", "Values": ["cloud-run"]}]
        )
        for sg in resp["SecurityGroups"]:
            # Check if any instances reference this SG
            inst_resp = self._ec2.describe_instances(
                Filters=[
                    {"Name": "instance.group-id", "Values": [sg["GroupId"]]},
                    {"Name": "instance-state-name", "Values": ["running", "pending", "stopping"]},
                ]
            )
            has_active = any(
                r["Instances"] for r in inst_resp["Reservations"]
            )
            if not has_active:
                if self._try_delete_security_group(sg["GroupId"]):
                    cleaned.append(f"SecurityGroup {sg['GroupId']} (orphaned)")

        return cleaned

    def find_instances_by_spec(self, spec: str) -> list[dict]:
        """Find running/pending instances tagged with the given spec file."""
        paginator = self._ec2.get_paginator("describe_instances")
        pages = paginator.paginate(
            Filters=[
                {"Name": "tag:cloud-run:spec", "Values": [spec[:256]]},
                {"Name": "instance-state-name", "Values": ["running", "pending"]},
            ]
        )
        results = []
        for page in pages:
            for res in page["Reservations"]:
                for inst in res["Instances"]:
                    tag_map = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                    results.append({
                        "instance_id": inst["InstanceId"],
                        "run_id": tag_map.get("cloud-run:run-id", ""),
                        "launched_at": tag_map.get("cloud-run:launched-at", ""),
                        "state": inst["State"]["Name"],
                    })
        return results

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _find_existing_instance(self, run_id: str) -> Optional[str]:
        """Return instance ID if a running/pending instance exists for this run_id."""
        paginator = self._ec2.get_paginator("describe_instances")
        pages = paginator.paginate(
            Filters=[
                {"Name": "tag:cloud-run:run-id", "Values": [run_id]},
                {"Name": "instance-state-name", "Values": ["running", "pending"]},
            ]
        )
        for page in pages:
            for res in page["Reservations"]:
                for inst in res["Instances"]:
                    return inst["InstanceId"]
        return None

    def _get_latest_ami(self) -> str:
        """Get latest Amazon Linux 2023 x86_64 AMI.

        Tries SSM parameter first, falls back to ec2:DescribeImages.
        """
        try:
            resp = self._ssm.get_parameter(Name=AL2023_SSM_PARAM)
            return resp["Parameter"]["Value"]
        except Exception:
            pass

        # Fallback: query EC2 directly
        resp = self._ec2.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name", "Values": ["al2023-ami-2023*-kernel-*-x86_64"]},
                {"Name": "state", "Values": ["available"]},
            ],
        )
        images = sorted(resp["Images"], key=lambda i: i["CreationDate"])
        if not images:
            raise RuntimeError("No AL2023 AMI found via DescribeImages")
        return images[-1]["ImageId"]

    def _ensure_security_group(self, run_id: str) -> str:
        """Create a security group for this run (SSH + all egress)."""
        sg_name = f"cloud-run-{run_id}"
        tags = {**RESOURCE_TAGS, "RunId": run_id}

        try:
            resp = self._ec2.create_security_group(
                GroupName=sg_name,
                Description=f"orchestration-kit cloud-run {run_id}",
                TagSpecifications=[
                    {
                        "ResourceType": "security-group",
                        "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
                    }
                ],
            )
            sg_id = resp["GroupId"]
        except self._ec2.exceptions.ClientError as e:
            if "InvalidGroup.Duplicate" in str(e):
                # Already exists — look it up
                resp = self._ec2.describe_security_groups(
                    Filters=[{"Name": "group-name", "Values": [sg_name]}]
                )
                return resp["SecurityGroups"][0]["GroupId"]
            raise

        # Allow SSH from anywhere (the instance is short-lived and key-protected)
        self._ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "SSH"}],
                }
            ],
        )

        return sg_id

    def _try_delete_security_group(self, sg_id: str) -> bool:
        """Attempt to delete a security group. Returns True if deleted."""
        try:
            self._ec2.delete_security_group(GroupId=sg_id)
            return True
        except Exception:
            return False

    def _render_user_data(self, config: InstanceConfig) -> str:
        """Render the bootstrap script with run-specific variables."""
        template = BOOTSTRAP_SCRIPT.read_text()

        # Inject variables at the top of the script
        var_block = "\n".join([
            f'export RUN_ID="{config.run_id}"',
            f'export S3_BUCKET="{S3_BUCKET}"',
            f'export S3_PREFIX="{S3_RUNS_PREFIX}/{config.run_id}"',
            f'export AWS_DEFAULT_REGION="{AWS_REGION}"',
            f'export EXPERIMENT_COMMAND="{config.command}"',
            f'export MAX_HOURS="{config.max_hours}"',
        ])

        # Forward local AWS credentials so the instance can access S3.
        # This is needed when no IAM instance profile is attached.
        import boto3 as _boto3
        _session = _boto3.Session()
        _creds = _session.get_credentials()
        if _creds:
            _frozen = _creds.get_frozen_credentials()
            var_block += f'\nexport AWS_ACCESS_KEY_ID="{_frozen.access_key}"'
            var_block += f'\nexport AWS_SECRET_ACCESS_KEY="{_frozen.secret_key}"'
            if _frozen.token:
                var_block += f'\nexport AWS_SESSION_TOKEN="{_frozen.token}"'

        # Add any extra env vars
        for k, v in config.env_vars.items():
            var_block += f'\nexport {k}="{v}"'

        # Insert after the shebang line
        lines = template.split("\n", 1)
        if lines[0].startswith("#!"):
            return lines[0] + "\n" + var_block + "\n" + lines[1]
        return var_block + "\n" + template
