"""Tests for cloud-run idempotency, tagging, duplicate detection, project state, and reaper."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

# Ensure tools/ is on the path so we can import cloud.*
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from cloud.backends.base import ComputeBackend, InstanceConfig
from cloud.backends.aws import AWSBackend
from cloud import state as project_state
from cloud.reaper import reap as reap_instances
from cloud.remote import DuplicateSpecError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> InstanceConfig:
    defaults = dict(
        instance_type="c7a.8xlarge",
        run_id="cloud-20260218T120000Z-abc12345",
        s3_prefix="s3://bucket/cloud-runs/cloud-20260218T120000Z-abc12345",
        command="echo hello",
        max_hours=4.0,
        use_spot=True,
        env_vars={},
        tags={"SpecFile": "specs/test.md"},
    )
    defaults.update(overrides)
    return InstanceConfig(**defaults)


def _mock_ec2_client():
    """Return a mock EC2 client with describe_instances paginator support."""
    client = mock.MagicMock()
    # Default: no existing instances
    paginator = mock.MagicMock()
    paginator.paginate.return_value = [{"Reservations": []}]
    client.get_paginator.return_value = paginator

    # Default run_instances response
    client.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-new123456"}]
    }

    # Exceptions class
    client.exceptions = mock.MagicMock()
    client.exceptions.ClientError = type("ClientError", (Exception,), {})

    return client


def _make_aws_backend(ec2=None, ssm=None):
    """Create an AWSBackend instance without calling __init__ (no boto3 needed)."""
    backend = AWSBackend.__new__(AWSBackend)
    backend._ec2 = ec2 or _mock_ec2_client()
    backend._ssm = ssm or mock.MagicMock()
    backend._ec2_resource = mock.MagicMock()
    # Default SSM response for AMI lookup
    backend._ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-test123"}}
    return backend


def _instance_entry(instance_id, run_id, spec="", launched_at="", state="running"):
    """Build a describe_instances-style instance dict."""
    return {
        "InstanceId": instance_id,
        "State": {"Name": state},
        "Tags": [
            {"Key": "cloud-run:run-id", "Value": run_id},
            {"Key": "cloud-run:spec", "Value": spec},
            {"Key": "cloud-run:launched-at", "Value": launched_at},
            {"Key": "cloud-run:max-hours", "Value": "4.0"},
        ],
        "SecurityGroups": [],
    }


# ---------------------------------------------------------------------------
# TestAWSTagging
# ---------------------------------------------------------------------------

class TestAWSTagging(unittest.TestCase):
    """Verify cloud-run:* tags, spec truncation, and ClientToken."""

    def test_tags_present_in_launch(self):
        ec2 = _mock_ec2_client()
        backend = _make_aws_backend(ec2=ec2)

        config = _make_config()
        with mock.patch("cloud.backends.aws.BOOTSTRAP_SCRIPT", mock.MagicMock()):
            with mock.patch.object(backend, "_render_user_data", return_value="#!/bin/bash\necho hi"):
                backend.provision(config)

        call_kwargs = ec2.run_instances.call_args[1]
        tag_specs = call_kwargs["TagSpecifications"]
        instance_tags = {
            t["Key"]: t["Value"]
            for t in tag_specs[0]["Tags"]
        }

        self.assertIn("cloud-run:run-id", instance_tags)
        self.assertEqual(instance_tags["cloud-run:run-id"], config.run_id)
        self.assertIn("cloud-run:spec", instance_tags)
        self.assertIn("cloud-run:max-hours", instance_tags)
        self.assertIn("cloud-run:launched-at", instance_tags)
        # Old tags still present
        self.assertIn("ManagedBy", instance_tags)
        self.assertIn("RunId", instance_tags)

    def test_spec_truncated_to_256(self):
        ec2 = _mock_ec2_client()
        backend = _make_aws_backend(ec2=ec2)

        long_spec = "x" * 500
        config = _make_config(tags={"SpecFile": long_spec})
        with mock.patch.object(backend, "_render_user_data", return_value="#!/bin/bash\necho hi"):
            backend.provision(config)

        call_kwargs = ec2.run_instances.call_args[1]
        instance_tags = {
            t["Key"]: t["Value"]
            for t in call_kwargs["TagSpecifications"][0]["Tags"]
        }
        self.assertLessEqual(len(instance_tags["cloud-run:spec"]), 256)

    def test_client_token_set_and_max_64(self):
        ec2 = _mock_ec2_client()
        backend = _make_aws_backend(ec2=ec2)

        config = _make_config()
        with mock.patch.object(backend, "_render_user_data", return_value="#!/bin/bash\necho hi"):
            backend.provision(config)

        call_kwargs = ec2.run_instances.call_args[1]
        self.assertIn("ClientToken", call_kwargs)
        self.assertLessEqual(len(call_kwargs["ClientToken"]), 64)
        self.assertTrue(call_kwargs["ClientToken"].startswith("cloud-run-"))


# ---------------------------------------------------------------------------
# TestSpotMarketOptions
# ---------------------------------------------------------------------------

class TestSpotMarketOptions(unittest.TestCase):
    """Verify InstanceMarketOptions presence based on use_spot flag."""

    def test_spot_options_present_when_spot(self):
        ec2 = _mock_ec2_client()
        backend = _make_aws_backend(ec2=ec2)

        config = _make_config(use_spot=True)
        with mock.patch.object(backend, "_render_user_data", return_value="#!/bin/bash\necho hi"):
            backend.provision(config)

        call_kwargs = ec2.run_instances.call_args[1]
        self.assertIn("InstanceMarketOptions", call_kwargs)
        self.assertEqual(call_kwargs["InstanceMarketOptions"]["MarketType"], "spot")

    def test_spot_options_absent_when_on_demand(self):
        ec2 = _mock_ec2_client()
        backend = _make_aws_backend(ec2=ec2)

        config = _make_config(use_spot=False)
        with mock.patch.object(backend, "_render_user_data", return_value="#!/bin/bash\necho hi"):
            backend.provision(config)

        call_kwargs = ec2.run_instances.call_args[1]
        self.assertNotIn("InstanceMarketOptions", call_kwargs)


# ---------------------------------------------------------------------------
# TestRunIdIdempotency
# ---------------------------------------------------------------------------

class TestRunIdIdempotency(unittest.TestCase):
    """Verify that existing instances are returned without new launch."""

    def test_existing_instance_returned(self):
        ec2 = _mock_ec2_client()
        backend = _make_aws_backend(ec2=ec2)

        # Simulate an existing instance found by _find_existing_instance
        existing_inst = _instance_entry("i-existing99", "cloud-20260218T120000Z-abc12345")
        paginator = mock.MagicMock()
        paginator.paginate.return_value = [
            {"Reservations": [{"Instances": [existing_inst]}]}
        ]
        ec2.get_paginator.return_value = paginator

        config = _make_config()
        instance_id = backend.provision(config)

        self.assertEqual(instance_id, "i-existing99")
        ec2.run_instances.assert_not_called()

    def test_no_existing_launches_new(self):
        ec2 = _mock_ec2_client()
        backend = _make_aws_backend(ec2=ec2)

        config = _make_config()
        with mock.patch.object(backend, "_render_user_data", return_value="#!/bin/bash\necho hi"):
            instance_id = backend.provision(config)

        self.assertEqual(instance_id, "i-new123456")
        ec2.run_instances.assert_called_once()


# ---------------------------------------------------------------------------
# TestSpecDuplicateDetection
# ---------------------------------------------------------------------------

class TestSpecDuplicateDetection(unittest.TestCase):
    """Verify spec-level duplicate detection in remote.run()."""

    def test_duplicate_spec_blocks_launch(self):
        backend = mock.MagicMock(spec=ComputeBackend)
        backend.find_instances_by_spec.return_value = [
            {"instance_id": "i-dup1", "run_id": "cloud-old", "launched_at": "", "state": "running"}
        ]

        from cloud import remote
        with mock.patch.object(remote, "_save_state"), \
             mock.patch.object(remote, "_update_state"), \
             mock.patch.object(remote, "_load_state", return_value={"instance_id": None}), \
             mock.patch.object(remote, "_generate_run_id", return_value="cloud-test-dup"):
            with self.assertRaises(DuplicateSpecError):
                remote.run(
                    command="echo test",
                    backend=backend,
                    backend_name="aws",
                    project_root="/tmp/fake",
                    spec_file="specs/experiment.md",
                    instance_type="c7a.8xlarge",
                    allow_duplicate=False,
                )

        backend.provision.assert_not_called()

    def test_allow_duplicate_bypasses_check(self):
        backend = mock.MagicMock(spec=ComputeBackend)
        backend.find_instances_by_spec.return_value = [
            {"instance_id": "i-dup1", "run_id": "cloud-old", "launched_at": "", "state": "running"}
        ]
        backend.provision.return_value = "i-new1"
        backend.wait_ready.return_value = None

        from cloud import remote
        with mock.patch.object(remote, "_save_state"), \
             mock.patch.object(remote, "_update_state"), \
             mock.patch.object(remote, "_load_state", return_value={"status": "running", "run_id": "cloud-test-ad", "project_root": "/tmp/fake"}), \
             mock.patch.object(remote, "_generate_run_id", return_value="cloud-test-ad"), \
             mock.patch.object(remote.s3_helper, "upload_code"), \
             mock.patch.object(remote.s3_helper, "check_exit_code", return_value=0), \
             mock.patch.object(remote.s3_helper, "download_results"), \
             mock.patch.object(remote.project_state, "register_run"), \
             mock.patch.object(remote.project_state, "remove_run"):
            result = remote.run(
                command="echo test",
                backend=backend,
                backend_name="aws",
                project_root="/tmp/fake",
                spec_file="specs/experiment.md",
                instance_type="c7a.8xlarge",
                allow_duplicate=True,
            )

        # find_instances_by_spec is still called but result is ignored
        backend.provision.assert_called_once()

    def test_no_spec_skips_duplicate_check(self):
        backend = mock.MagicMock(spec=ComputeBackend)
        backend.provision.return_value = "i-new2"
        backend.wait_ready.return_value = None

        from cloud import remote
        with mock.patch.object(remote, "_save_state"), \
             mock.patch.object(remote, "_update_state"), \
             mock.patch.object(remote, "_load_state", return_value={"status": "running", "run_id": "cloud-test-ns", "project_root": "/tmp/fake"}), \
             mock.patch.object(remote, "_generate_run_id", return_value="cloud-test-ns"), \
             mock.patch.object(remote.s3_helper, "upload_code"), \
             mock.patch.object(remote.s3_helper, "check_exit_code", return_value=0), \
             mock.patch.object(remote.s3_helper, "download_results"), \
             mock.patch.object(remote.project_state, "register_run"), \
             mock.patch.object(remote.project_state, "remove_run"):
            result = remote.run(
                command="echo test",
                backend=backend,
                backend_name="aws",
                project_root="/tmp/fake",
                spec_file=None,
                instance_type="c7a.8xlarge",
            )

        backend.find_instances_by_spec.assert_not_called()
        backend.provision.assert_called_once()


# ---------------------------------------------------------------------------
# TestProjectLocalState
# ---------------------------------------------------------------------------

class TestProjectLocalState(unittest.TestCase):
    """Verify .kit/cloud-state.json management."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.project_root = self._tmpdir

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_register_creates_file(self):
        project_state.register_run(
            self.project_root, "run-1",
            instance_id="i-abc", backend="aws",
            instance_type="c7a.8xlarge", spec_file="spec.md",
            launched_at="2026-02-18T12:00:00+00:00", max_hours=4.0,
        )
        state_path = Path(self.project_root) / ".kit" / "cloud-state.json"
        self.assertTrue(state_path.exists())
        data = json.loads(state_path.read_text())
        self.assertIn("run-1", data["active_runs"])
        self.assertEqual(data["active_runs"]["run-1"]["instance_id"], "i-abc")

    def test_remove_cleans_entry(self):
        project_state.register_run(
            self.project_root, "run-1",
            instance_id="i-abc", backend="aws", instance_type="c7a.8xlarge",
        )
        project_state.register_run(
            self.project_root, "run-2",
            instance_id="i-def", backend="aws", instance_type="c7a.4xlarge",
        )
        project_state.remove_run(self.project_root, "run-1")

        runs = project_state.list_active_runs(self.project_root)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], "run-2")

    def test_list_returns_all(self):
        for i in range(3):
            project_state.register_run(
                self.project_root, f"run-{i}",
                instance_id=f"i-{i}", backend="aws", instance_type="c7a.8xlarge",
            )
        runs = project_state.list_active_runs(self.project_root)
        self.assertEqual(len(runs), 3)

    def test_get_run_returns_entry(self):
        project_state.register_run(
            self.project_root, "run-x",
            instance_id="i-x", backend="aws", instance_type="c7a.8xlarge",
        )
        entry = project_state.get_run(self.project_root, "run-x")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["instance_id"], "i-x")

    def test_get_run_returns_none_for_missing(self):
        entry = project_state.get_run(self.project_root, "nonexistent")
        self.assertIsNone(entry)

    def test_update_run_merges(self):
        project_state.register_run(
            self.project_root, "run-u",
            instance_id="i-u", backend="aws", instance_type="c7a.8xlarge",
        )
        project_state.update_run(self.project_root, "run-u", status="terminated")
        entry = project_state.get_run(self.project_root, "run-u")
        self.assertEqual(entry["status"], "terminated")

    def test_missing_file_returns_empty(self):
        runs = project_state.list_active_runs(self.project_root)
        self.assertEqual(runs, [])

    def test_corrupt_json_returns_empty(self):
        kit_dir = Path(self.project_root) / ".kit"
        kit_dir.mkdir(parents=True, exist_ok=True)
        (kit_dir / "cloud-state.json").write_text("{invalid json!!")
        runs = project_state.list_active_runs(self.project_root)
        self.assertEqual(runs, [])

    def test_remove_on_missing_is_noop(self):
        # Should not raise
        project_state.remove_run(self.project_root, "nonexistent")


# ---------------------------------------------------------------------------
# TestReaper
# ---------------------------------------------------------------------------

class TestReaper(unittest.TestCase):
    """Verify instance reaper logic."""

    def _make_backend_with_instances(self, instances):
        """Create a mock backend whose paginator returns the given instances."""
        backend = mock.MagicMock()
        ec2 = mock.MagicMock()
        backend._ec2 = ec2

        paginator = mock.MagicMock()
        paginator.paginate.return_value = [
            {"Reservations": [{"Instances": instances}]}
        ]
        ec2.get_paginator.return_value = paginator
        return backend

    def test_expired_lease_reaped(self):
        launched = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        inst = _instance_entry("i-expired", "run-exp", launched_at=launched)
        backend = self._make_backend_with_instances([inst])

        actions = reap_instances(backend, hard_ceiling_hours=24)

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["instance_id"], "i-expired")
        self.assertIn("lease_expired", actions[0]["reason"])
        self.assertEqual(actions[0]["action"], "terminated")
        backend.terminate.assert_called_once_with("i-expired")

    def test_hard_ceiling_reaped(self):
        launched = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        inst = _instance_entry("i-old", "run-old", launched_at=launched)
        # Set max_hours higher so lease isn't the trigger
        inst["Tags"] = [
            {"Key": "cloud-run:run-id", "Value": "run-old"},
            {"Key": "cloud-run:spec", "Value": ""},
            {"Key": "cloud-run:launched-at", "Value": launched},
            {"Key": "cloud-run:max-hours", "Value": "48"},
        ]
        backend = self._make_backend_with_instances([inst])

        actions = reap_instances(backend, hard_ceiling_hours=24)

        self.assertEqual(len(actions), 1)
        self.assertIn("hard_ceiling", actions[0]["reason"])
        self.assertEqual(actions[0]["action"], "terminated")

    def test_dry_run_does_not_terminate(self):
        launched = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        inst = _instance_entry("i-dry", "run-dry", launched_at=launched)
        backend = self._make_backend_with_instances([inst])

        actions = reap_instances(backend, dry_run=True)

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "would_terminate")
        backend.terminate.assert_not_called()

    def test_healthy_instance_untouched(self):
        # Launched 1 hour ago, max_hours=4 → healthy
        launched = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        inst = _instance_entry("i-healthy", "run-healthy", launched_at=launched)
        backend = self._make_backend_with_instances([inst])

        actions = reap_instances(backend, hard_ceiling_hours=24)

        self.assertEqual(len(actions), 0)
        backend.terminate.assert_not_called()

    def test_non_aws_backend_returns_empty(self):
        # Backend without _ec2 attribute
        backend = mock.MagicMock(spec=ComputeBackend)
        del backend._ec2

        actions = reap_instances(backend)
        self.assertEqual(actions, [])


if __name__ == "__main__":
    unittest.main()
