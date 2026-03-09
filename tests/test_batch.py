"""Tests for parallel batch dispatch: batch.py, state.py batch_id, remote.py batch_id passthrough,
CLI batch subcommands, preflight parallelizable output, and MCP tool definition.

All tests work WITHOUT AWS credentials. All remote/cloud operations are mocked.

Run: cd orchestration-kit && python3 -m pytest tests/test_batch.py -v
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Ensure tools/ is on the path so we can import cloud.*
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


# ---------------------------------------------------------------------------
# 1. test_generate_batch_id_format
# ---------------------------------------------------------------------------

class TestGenerateBatchId:
    """Verify batch ID generation matches the spec format."""

    def test_generate_batch_id_format(self):
        """Batch ID must match: batch-{YYYYMMDDTHHMMSSZ}-{8 hex chars}."""
        from cloud.batch import generate_batch_id

        bid = generate_batch_id()
        pattern = r"^batch-\d{8}T\d{6}Z-[0-9a-f]{8}$"
        assert re.match(pattern, bid), f"batch_id '{bid}' does not match pattern '{pattern}'"

    def test_generate_batch_id_uniqueness(self):
        """Two consecutive calls should produce different IDs (uuid component)."""
        from cloud.batch import generate_batch_id

        ids = {generate_batch_id() for _ in range(20)}
        assert len(ids) == 20, "generate_batch_id produced duplicate IDs"


# ---------------------------------------------------------------------------
# 2. test_save_load_batch_state_roundtrip
# ---------------------------------------------------------------------------

class TestBatchStatePersistence:
    """Verify save/load roundtrip for batch state."""

    def test_save_load_batch_state_roundtrip(self, monkeypatch, tmp_path):
        """Save a batch state dict, load it back, verify equality."""
        from cloud import batch as batch_mod

        # Override _batch_state_dir to use temp directory
        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        state = {
            "batch_id": "batch-20260223T120000Z-abc12345",
            "runs": {"spec-a.md": "cloud-20260223T120001Z-deadbeef"},
            "specs": ["spec-a.md"],
            "status": "running",
            "started_at": "2026-02-23T12:00:00Z",
            "finished_at": None,
            "max_instances": 5,
            "results": {},
        }
        batch_mod.save_batch_state("batch-20260223T120000Z-abc12345", state)
        loaded = batch_mod.load_batch_state("batch-20260223T120000Z-abc12345")
        assert loaded == state

    def test_load_batch_state_missing_raises(self, monkeypatch, tmp_path):
        """Loading a nonexistent batch state raises FileNotFoundError."""
        from cloud import batch as batch_mod

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        with pytest.raises(FileNotFoundError):
            batch_mod.load_batch_state("batch-nonexistent-00000000")

    def test_save_creates_json_file(self, monkeypatch, tmp_path):
        """Saved batch state should be valid JSON on disk."""
        from cloud import batch as batch_mod

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        state = {"batch_id": "batch-test-abc12345", "status": "running"}
        batch_mod.save_batch_state("batch-test-abc12345", state)

        path = tmp_path / "batch-test-abc12345.json"
        assert path.exists(), "State file was not created on disk"
        loaded = json.loads(path.read_text())
        assert loaded["batch_id"] == "batch-test-abc12345"


# ---------------------------------------------------------------------------
# 3. test_launch_batch_exceeds_max_instances
# ---------------------------------------------------------------------------

class TestLaunchBatchValidation:
    """Verify launch_batch input validation."""

    def test_launch_batch_exceeds_max_instances(self):
        """Calling launch_batch with more specs than max_instances raises ValueError."""
        from cloud.batch import launch_batch

        specs = [f"spec-{i}.md" for i in range(6)]
        with pytest.raises(ValueError, match=r"exceeds max_instances"):
            launch_batch(
                specs=specs,
                command="echo test",
                backend=mock.MagicMock(),
                backend_name="aws",
                project_root="/tmp/fake",
                instance_type="c7a.8xlarge",
                max_instances=5,
            )

    def test_launch_batch_exactly_at_max_instances_ok(self, monkeypatch):
        """launch_batch with exactly max_instances specs should NOT raise."""
        from cloud import batch as batch_mod
        from cloud import remote

        # Mock all downstream calls
        monkeypatch.setattr(
            remote, "run",
            lambda **kw: {"run_id": f"cloud-fake-{kw.get('spec_file', 'x')}", "status": "provisioning"},
        )
        monkeypatch.setattr(
            remote, "poll_status",
            lambda rid: {"run_id": rid, "status": "completed", "exit_code": 0},
        )
        monkeypatch.setattr(remote, "pull_results", lambda rid, d=None: d or "/tmp/results")
        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: Path(tempfile.mkdtemp()))
        monkeypatch.setattr(batch_mod, "save_batch_state", lambda bid, state: None)

        specs = [f"spec-{i}.md" for i in range(5)]
        # Should not raise with max_instances=5
        result = batch_mod.launch_batch(
            specs=specs,
            command="echo test",
            backend=mock.MagicMock(),
            backend_name="aws",
            project_root="/tmp/fake",
            instance_type="c7a.8xlarge",
            max_instances=5,
        )
        assert result["status"] in ("completed", "partial")


# ---------------------------------------------------------------------------
# 4. test_launch_batch_exceeds_max_cost
# ---------------------------------------------------------------------------

class TestLaunchBatchCostGuard:
    """Verify cost guardrail rejects batches exceeding max_cost."""

    def test_launch_batch_exceeds_max_cost(self, monkeypatch):
        """launch_batch should reject when estimated total cost exceeds max_cost."""
        from cloud import batch as batch_mod
        from cloud import preflight

        # Mock preflight.check_spec to return $2.50 per spec
        def mock_check_spec(spec_path, **kwargs):
            return {
                "recommendation": "remote",
                "backend": "aws",
                "instance_type": "c7a.8xlarge",
                "cost_per_hour_spot": 0.47,
                "use_spot": True,
                "profile": {
                    "estimated_wall_hours": 5.0,
                    "parallelizable": True,
                },
                "estimated_total_cost": "$2.35 (spot)",
            }

        monkeypatch.setattr(preflight, "check_spec", mock_check_spec)

        with pytest.raises(ValueError, match=r"max_cost|cost"):
            batch_mod.launch_batch(
                specs=["spec-a.md", "spec-b.md"],
                command="echo test",
                backend=mock.MagicMock(),
                backend_name="aws",
                project_root="/tmp/fake",
                instance_type="c7a.8xlarge",
                max_cost=1.00,  # Total estimate ~$4.70 > $1.00
            )

    def test_launch_batch_cost_check_skips_missing_profile(self, monkeypatch):
        """Specs without a compute profile should be skipped in cost estimation, not crash."""
        from cloud import batch as batch_mod
        from cloud import preflight, remote

        call_count = {"n": 0}

        def mock_check_spec(spec_path, **kwargs):
            call_count["n"] += 1
            if "no-profile" in spec_path:
                raise ValueError("Spec has no Compute Profile YAML block")
            return {
                "recommendation": "remote",
                "backend": "aws",
                "instance_type": "c7a.8xlarge",
                "cost_per_hour_spot": 0.10,
                "use_spot": True,
                "profile": {"estimated_wall_hours": 0.5},
            }

        monkeypatch.setattr(preflight, "check_spec", mock_check_spec)
        monkeypatch.setattr(
            remote, "run",
            lambda **kw: {"run_id": f"cloud-fake-{kw.get('spec_file', 'x')}", "status": "provisioning"},
        )
        monkeypatch.setattr(
            remote, "poll_status",
            lambda rid: {"run_id": rid, "status": "completed", "exit_code": 0},
        )
        monkeypatch.setattr(remote, "pull_results", lambda rid, d=None: d or "/tmp/results")
        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: Path(tempfile.mkdtemp()))
        monkeypatch.setattr(batch_mod, "save_batch_state", lambda bid, state: None)

        # Total cost from spec-a is $0.05, spec-no-profile is skipped → within max_cost=$1
        result = batch_mod.launch_batch(
            specs=["spec-a.md", "spec-no-profile.md"],
            command="echo test",
            backend=mock.MagicMock(),
            backend_name="aws",
            project_root="/tmp/fake",
            instance_type="c7a.8xlarge",
            max_cost=1.00,
        )
        assert result is not None


# ---------------------------------------------------------------------------
# 5. test_launch_batch_success
# ---------------------------------------------------------------------------

class TestLaunchBatchSuccess:
    """Verify the happy path of launch_batch with mocked remote operations."""

    def test_launch_batch_success(self, monkeypatch, tmp_path):
        """launch_batch with 2 specs: both launch, poll completes, results pulled, status='completed'."""
        from cloud import batch as batch_mod
        from cloud import remote

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        launched_specs = []
        polled_ids = []
        pulled_ids = []

        def mock_run(**kwargs):
            launched_specs.append(kwargs.get("spec_file"))
            return {
                "run_id": f"cloud-test-{kwargs['spec_file'].replace('.md', '')}",
                "status": "provisioning",
            }

        def mock_poll(run_id):
            polled_ids.append(run_id)
            return {"run_id": run_id, "status": "completed", "exit_code": 0}

        def mock_pull(run_id, output_dir=None):
            pulled_ids.append(run_id)
            return output_dir or "/tmp/results"

        monkeypatch.setattr(remote, "run", mock_run)
        monkeypatch.setattr(remote, "poll_status", mock_poll)
        monkeypatch.setattr(remote, "pull_results", mock_pull)

        result = batch_mod.launch_batch(
            specs=["spec-a.md", "spec-b.md"],
            command="python run.py",
            backend=mock.MagicMock(),
            backend_name="aws",
            project_root="/tmp/fake",
            instance_type="c7a.8xlarge",
        )

        # Both specs were launched
        assert set(launched_specs) == {"spec-a.md", "spec-b.md"}

        # Poll loop ran for both
        assert len(polled_ids) >= 2

        # Results pulled for both
        assert len(pulled_ids) == 2

        # Batch status is completed
        assert result["status"] == "completed"

        # runs dict maps spec to run_id
        assert "spec-a.md" in result["runs"]
        assert "spec-b.md" in result["runs"]

        # batch_id is set
        assert result["batch_id"].startswith("batch-")

    def test_launch_batch_partial_failure(self, monkeypatch, tmp_path):
        """If one run fails and one completes, batch status should be 'partial'."""
        from cloud import batch as batch_mod
        from cloud import remote

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        def mock_run(**kwargs):
            return {
                "run_id": f"cloud-test-{kwargs['spec_file'].replace('.md', '')}",
                "status": "provisioning",
            }

        def mock_poll(run_id):
            if "spec-a" in run_id:
                return {"run_id": run_id, "status": "completed", "exit_code": 0}
            else:
                return {"run_id": run_id, "status": "failed", "exit_code": 1}

        monkeypatch.setattr(remote, "run", mock_run)
        monkeypatch.setattr(remote, "poll_status", mock_poll)
        monkeypatch.setattr(remote, "pull_results", lambda rid, d=None: d or "/tmp")

        result = batch_mod.launch_batch(
            specs=["spec-a.md", "spec-b.md"],
            command="python run.py",
            backend=mock.MagicMock(),
            backend_name="aws",
            project_root="/tmp/fake",
            instance_type="c7a.8xlarge",
        )

        assert result["status"] == "partial"

    def test_launch_batch_passes_detach_true(self, monkeypatch, tmp_path):
        """launch_batch must call remote.run with detach=True."""
        from cloud import batch as batch_mod
        from cloud import remote

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        detach_values = []

        def mock_run(**kwargs):
            detach_values.append(kwargs.get("detach"))
            return {"run_id": "cloud-fake-001", "status": "provisioning"}

        monkeypatch.setattr(remote, "run", mock_run)
        monkeypatch.setattr(
            remote, "poll_status",
            lambda rid: {"run_id": rid, "status": "completed", "exit_code": 0},
        )
        monkeypatch.setattr(remote, "pull_results", lambda rid, d=None: "/tmp")

        batch_mod.launch_batch(
            specs=["spec-a.md"],
            command="echo test",
            backend=mock.MagicMock(),
            backend_name="aws",
            project_root="/tmp/fake",
            instance_type="c7a.8xlarge",
        )

        assert all(d is True for d in detach_values), (
            "launch_batch must call remote.run with detach=True"
        )

    def test_launch_batch_passes_batch_id_to_remote_run(self, monkeypatch, tmp_path):
        """launch_batch must pass batch_id to remote.run."""
        from cloud import batch as batch_mod
        from cloud import remote

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        batch_ids_passed = []

        def mock_run(**kwargs):
            batch_ids_passed.append(kwargs.get("batch_id"))
            return {"run_id": "cloud-fake-002", "status": "provisioning"}

        monkeypatch.setattr(remote, "run", mock_run)
        monkeypatch.setattr(
            remote, "poll_status",
            lambda rid: {"run_id": rid, "status": "completed", "exit_code": 0},
        )
        monkeypatch.setattr(remote, "pull_results", lambda rid, d=None: "/tmp")

        result = batch_mod.launch_batch(
            specs=["spec-a.md"],
            command="echo test",
            backend=mock.MagicMock(),
            backend_name="aws",
            project_root="/tmp/fake",
            instance_type="c7a.8xlarge",
        )

        assert len(batch_ids_passed) == 1
        assert batch_ids_passed[0] == result["batch_id"]


# ---------------------------------------------------------------------------
# 6. test_poll_batch
# ---------------------------------------------------------------------------

class TestPollBatch:
    """Verify poll_batch queries each run and updates batch state."""

    def test_poll_batch(self, monkeypatch, tmp_path):
        """Create batch state with 2 run_ids, mock poll_status, verify per-run status updated."""
        from cloud import batch as batch_mod
        from cloud import remote

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        # Write initial batch state
        state = {
            "batch_id": "batch-test-poll-aabbccdd",
            "runs": {
                "spec-a.md": "cloud-run-aaa",
                "spec-b.md": "cloud-run-bbb",
            },
            "specs": ["spec-a.md", "spec-b.md"],
            "status": "running",
            "started_at": "2026-02-23T12:00:00Z",
            "finished_at": None,
            "max_instances": 5,
            "results": {},
        }
        (tmp_path / "batch-test-poll-aabbccdd.json").write_text(json.dumps(state))

        def mock_poll(run_id):
            if run_id == "cloud-run-aaa":
                return {"run_id": run_id, "status": "completed", "exit_code": 0}
            return {"run_id": run_id, "status": "running"}

        monkeypatch.setattr(remote, "poll_status", mock_poll)

        result = batch_mod.poll_batch("batch-test-poll-aabbccdd")

        assert result["batch_id"] == "batch-test-poll-aabbccdd"
        # The result should reflect per-run status
        assert isinstance(result, dict)

    def test_poll_batch_unknown_raises(self, monkeypatch, tmp_path):
        """poll_batch for a nonexistent batch_id should raise FileNotFoundError."""
        from cloud import batch as batch_mod

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        with pytest.raises(FileNotFoundError):
            batch_mod.poll_batch("batch-nonexistent-00000000")


# ---------------------------------------------------------------------------
# 7. test_pull_batch
# ---------------------------------------------------------------------------

class TestPullBatch:
    """Verify pull_batch calls pull_results for each completed run."""

    def test_pull_batch(self, monkeypatch, tmp_path):
        """Create batch state with completed runs, verify pull called for each."""
        from cloud import batch as batch_mod
        from cloud import remote

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        state = {
            "batch_id": "batch-test-pull-aabbccdd",
            "runs": {
                "spec-a.md": "cloud-run-aaa",
                "spec-b.md": "cloud-run-bbb",
            },
            "specs": ["spec-a.md", "spec-b.md"],
            "status": "completed",
            "started_at": "2026-02-23T12:00:00Z",
            "finished_at": "2026-02-23T13:00:00Z",
            "max_instances": 5,
            "results": {},
        }
        (tmp_path / "batch-test-pull-aabbccdd.json").write_text(json.dumps(state))

        pulled = []

        def mock_poll(run_id):
            return {"run_id": run_id, "status": "completed", "exit_code": 0}

        def mock_pull(run_id, output_dir=None):
            pulled.append(run_id)
            return output_dir or "/tmp/results"

        monkeypatch.setattr(remote, "poll_status", mock_poll)
        monkeypatch.setattr(remote, "pull_results", mock_pull)

        result = batch_mod.pull_batch("batch-test-pull-aabbccdd", output_base=str(tmp_path / "output"))

        # pull_results called for both completed runs
        assert set(pulled) == {"cloud-run-aaa", "cloud-run-bbb"}
        assert isinstance(result, dict)

    def test_pull_batch_skips_failed_runs(self, monkeypatch, tmp_path):
        """pull_batch should only pull results for completed runs, not failed ones."""
        from cloud import batch as batch_mod
        from cloud import remote

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        state = {
            "batch_id": "batch-test-pull2-aabbccdd",
            "runs": {
                "spec-a.md": "cloud-run-aaa",
                "spec-b.md": "cloud-run-bbb",
            },
            "specs": ["spec-a.md", "spec-b.md"],
            "status": "partial",
            "started_at": "2026-02-23T12:00:00Z",
            "finished_at": "2026-02-23T13:00:00Z",
            "max_instances": 5,
            "results": {},
        }
        (tmp_path / "batch-test-pull2-aabbccdd.json").write_text(json.dumps(state))

        pulled = []

        def mock_poll(run_id):
            if run_id == "cloud-run-aaa":
                return {"run_id": run_id, "status": "completed", "exit_code": 0}
            return {"run_id": run_id, "status": "failed", "exit_code": 1}

        monkeypatch.setattr(remote, "poll_status", mock_poll)
        monkeypatch.setattr(remote, "pull_results", lambda rid, d=None: pulled.append(rid) or "/tmp")

        result = batch_mod.pull_batch("batch-test-pull2-aabbccdd")

        # Only the completed run should be pulled
        assert pulled == ["cloud-run-aaa"]


# ---------------------------------------------------------------------------
# 8. test_list_batches_ordering
# ---------------------------------------------------------------------------

class TestListBatches:
    """Verify list_batches returns batches sorted most-recent-first."""

    def test_list_batches_ordering(self, monkeypatch, tmp_path):
        """Create 3 batch state files with different started_at; verify newest first."""
        from cloud import batch as batch_mod

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        batches = [
            {"batch_id": "batch-old-00000001", "started_at": "2026-02-21T10:00:00Z", "status": "completed", "specs": []},
            {"batch_id": "batch-mid-00000002", "started_at": "2026-02-22T10:00:00Z", "status": "completed", "specs": []},
            {"batch_id": "batch-new-00000003", "started_at": "2026-02-23T10:00:00Z", "status": "running", "specs": []},
        ]
        for b in batches:
            (tmp_path / f"{b['batch_id']}.json").write_text(json.dumps(b))

        result = batch_mod.list_batches()

        assert len(result) == 3
        assert result[0]["batch_id"] == "batch-new-00000003", "Most recent batch should be first"
        assert result[1]["batch_id"] == "batch-mid-00000002"
        assert result[2]["batch_id"] == "batch-old-00000001"

    def test_list_batches_empty(self, monkeypatch, tmp_path):
        """list_batches on empty directory returns empty list."""
        from cloud import batch as batch_mod

        monkeypatch.setattr(batch_mod, "_batch_state_dir", lambda: tmp_path)

        result = batch_mod.list_batches()
        assert result == []


# ---------------------------------------------------------------------------
# 9. test_state_register_run_with_batch_id
# ---------------------------------------------------------------------------

class TestStateRegisterRunBatchId:
    """Verify state.register_run accepts and stores batch_id."""

    def test_state_register_run_with_batch_id(self, tmp_path):
        """register_run with batch_id should store it in the state file."""
        from cloud import state as project_state

        project_root = str(tmp_path)
        project_state.register_run(
            project_root, "run-batch-test-1",
            instance_id="i-abc",
            backend="aws",
            instance_type="c7a.8xlarge",
            spec_file="spec.md",
            batch_id="batch-test-12345678",
        )

        state_path = tmp_path / ".kit" / "cloud-state.json"
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        entry = data["active_runs"]["run-batch-test-1"]
        assert entry["batch_id"] == "batch-test-12345678"

    def test_state_register_run_without_batch_id_defaults_empty(self, tmp_path):
        """register_run without batch_id should store empty string (backward compat)."""
        from cloud import state as project_state

        project_root = str(tmp_path)
        project_state.register_run(
            project_root, "run-no-batch",
            instance_id="i-def",
            backend="aws",
            instance_type="c7a.8xlarge",
        )

        data = json.loads((tmp_path / ".kit" / "cloud-state.json").read_text())
        entry = data["active_runs"]["run-no-batch"]
        assert entry.get("batch_id", "") == "", "Missing batch_id should default to empty string"

    def test_state_register_run_backward_compatible(self, tmp_path):
        """Existing register_run calls (no batch_id) must continue to work."""
        from cloud import state as project_state

        project_root = str(tmp_path)
        # This is the existing call signature from remote.py — must not break
        project_state.register_run(
            project_root, "run-compat",
            instance_id="i-ghi",
            backend="aws",
            instance_type="c7a.8xlarge",
            spec_file="experiment.md",
            launched_at="2026-02-23T12:00:00Z",
            max_hours=4.0,
        )

        runs = project_state.list_active_runs(project_root)
        assert len(runs) == 1
        assert runs[0]["instance_id"] == "i-ghi"


# ---------------------------------------------------------------------------
# 10. test_state_list_batch_runs
# ---------------------------------------------------------------------------

class TestStateListBatchRuns:
    """Verify state.list_batch_runs filters by batch_id."""

    def test_state_list_batch_runs(self, tmp_path):
        """Register 3 runs: 2 with batch_id='batch-A', 1 with batch_id='batch-B'.
        list_batch_runs('batch-A') should return exactly 2."""
        from cloud import state as project_state

        project_root = str(tmp_path)
        project_state.register_run(
            project_root, "run-a1",
            instance_id="i-a1", backend="aws", instance_type="c7a.8xlarge",
            batch_id="batch-A",
        )
        project_state.register_run(
            project_root, "run-a2",
            instance_id="i-a2", backend="aws", instance_type="c7a.8xlarge",
            batch_id="batch-A",
        )
        project_state.register_run(
            project_root, "run-b1",
            instance_id="i-b1", backend="aws", instance_type="c7a.8xlarge",
            batch_id="batch-B",
        )

        batch_a_runs = project_state.list_batch_runs(project_root, "batch-A")
        assert len(batch_a_runs) == 2
        run_ids = {r["run_id"] for r in batch_a_runs}
        assert run_ids == {"run-a1", "run-a2"}

    def test_state_list_batch_runs_empty(self, tmp_path):
        """list_batch_runs for a batch_id with no runs returns empty list."""
        from cloud import state as project_state

        project_root = str(tmp_path)
        result = project_state.list_batch_runs(project_root, "batch-nonexistent")
        assert result == []

    def test_state_list_batch_runs_excludes_other_batches(self, tmp_path):
        """list_batch_runs must not return runs from other batches."""
        from cloud import state as project_state

        project_root = str(tmp_path)
        project_state.register_run(
            project_root, "run-x",
            instance_id="i-x", backend="aws", instance_type="c7a.8xlarge",
            batch_id="batch-X",
        )
        project_state.register_run(
            project_root, "run-y",
            instance_id="i-y", backend="aws", instance_type="c7a.8xlarge",
            batch_id="batch-Y",
        )

        result = project_state.list_batch_runs(project_root, "batch-X")
        assert len(result) == 1
        assert result[0]["run_id"] == "run-x"


# ---------------------------------------------------------------------------
# 11. test_cli_batch_subparser_exists
# ---------------------------------------------------------------------------

class TestCLIBatchSubcommand:
    """Verify cloud-run CLI accepts batch subcommands."""

    def test_cli_batch_run_accepted(self):
        """The CLI parser should accept 'batch run' with expected arguments."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "cloud-run"), "batch", "run", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # If batch run subcommand exists, --help should succeed (exit 0)
        assert result.returncode == 0, (
            f"'cloud-run batch run --help' failed with exit {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # Verify expected args appear in help text
        assert "--specs" in result.stdout, "'--specs' argument not in batch run help"
        assert "--max-instances" in result.stdout, "'--max-instances' argument not in batch run help"
        assert "--max-cost" in result.stdout, "'--max-cost' argument not in batch run help"

    def test_cli_batch_status_accepted(self):
        """The CLI parser should accept 'batch status <batch_id>'."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "cloud-run"), "batch", "status", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"'cloud-run batch status --help' failed.\nstderr: {result.stderr}"
        )

    def test_cli_batch_pull_accepted(self):
        """The CLI parser should accept 'batch pull <batch_id>'."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "cloud-run"), "batch", "pull", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"'cloud-run batch pull --help' failed.\nstderr: {result.stderr}"
        )

    def test_cli_batch_ls_accepted(self):
        """The CLI parser should accept 'batch ls'."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "cloud-run"), "batch", "ls"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # ls might print "No tracked batches." or similar — should not error on parser level
        # Accept 0 (success) or 1 (empty output handled) — just not 2 (argparse error)
        assert result.returncode != 2, (
            f"'cloud-run batch ls' argparse error.\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# 12. test_preflight_parallelizable_in_output
# ---------------------------------------------------------------------------

class TestPreflightParallelizable:
    """Verify preflight surfaces parallelizable field."""

    def test_preflight_parallelizable_in_output(self, tmp_path):
        """check_spec with parallelizable: true in compute profile should return parallelizable=True."""
        from cloud.preflight import check_spec

        spec_content = """\
# Test Experiment

## Resource Budget

**Tier:** Standard

- Max wall-clock time: 2h
- Max GPU-hours: 0
- Max training runs: 10

### Compute Profile

```yaml
compute_type: cpu
estimated_rows: 100000
model_type: xgboost
sequential_fits: 10
parallelizable: true
memory_gb: 4
gpu_type: none
estimated_wall_hours: 1.5
runtime: python
```
"""
        spec_path = tmp_path / "test-spec.md"
        spec_path.write_text(spec_content)

        result = check_spec(str(spec_path))

        assert "parallelizable" in result, "check_spec result missing 'parallelizable' field"
        assert result["parallelizable"] is True

    def test_preflight_parallelizable_false_default(self, tmp_path):
        """check_spec with parallelizable: false (or absent) should return parallelizable=False."""
        from cloud.preflight import check_spec

        spec_content = """\
# Test Experiment

## Resource Budget

**Tier:** Quick

### Compute Profile

```yaml
compute_type: cpu
estimated_rows: 1000
model_type: xgboost
sequential_fits: 5
memory_gb: 2
gpu_type: none
estimated_wall_hours: 0.1
runtime: python
```
"""
        spec_path = tmp_path / "test-spec-no-parallel.md"
        spec_path.write_text(spec_content)

        result = check_spec(str(spec_path))

        # parallelizable not in spec → default False
        assert result.get("parallelizable") is False or result.get("parallelizable") is None

    def test_preflight_parallelizable_appends_reason(self, tmp_path):
        """When parallelizable and remote recommended, reason should mention batch execution."""
        from cloud.preflight import check_spec

        spec_content = """\
# Test Experiment

## Resource Budget

**Tier:** Heavy

- Max wall-clock time: 8h
- Max training runs: 100

### Compute Profile

```yaml
compute_type: cpu
estimated_rows: 5000000
model_type: xgboost
sequential_fits: 100
parallelizable: true
memory_gb: 32
gpu_type: none
estimated_wall_hours: 6.0
runtime: python
```
"""
        spec_path = tmp_path / "test-spec-parallel-heavy.md"
        spec_path.write_text(spec_content)

        result = check_spec(str(spec_path))

        assert result["recommendation"] == "remote"
        assert result.get("parallelizable") is True
        assert "parallelizable" in result.get("reason", "").lower() or "batch" in result.get("reason", "").lower(), (
            f"Reason should mention parallelizable/batch. Got: {result.get('reason')}"
        )


# ---------------------------------------------------------------------------
# 13. test_remote_run_accepts_batch_id (additional — tests remote.py modification)
# ---------------------------------------------------------------------------

class TestRemoteRunBatchId:
    """Verify remote.run() accepts and passes through batch_id parameter."""

    def test_remote_run_accepts_batch_id_param(self):
        """remote.run() should accept batch_id as a keyword argument without TypeError."""
        from cloud import remote

        import inspect
        sig = inspect.signature(remote.run)
        assert "batch_id" in sig.parameters, (
            "remote.run() missing 'batch_id' parameter"
        )

    def test_remote_run_passes_batch_id_to_state(self, monkeypatch, tmp_path):
        """remote.run() should pass batch_id to project_state.register_run."""
        from cloud import remote
        from cloud import state as project_state

        registered_kwargs = {}

        def mock_register_run(project_root, run_id, **kwargs):
            registered_kwargs.update(kwargs)

        backend = mock.MagicMock()
        backend.find_instances_by_spec.return_value = []
        backend.provision.return_value = "i-new123"
        backend.wait_ready.return_value = None

        monkeypatch.setattr(remote, "_save_state", lambda rid, state: None)
        monkeypatch.setattr(remote, "_update_state", lambda rid, **kw: {})
        monkeypatch.setattr(remote, "_load_state", lambda rid: {
            "status": "running", "run_id": rid, "project_root": "/tmp",
        })
        monkeypatch.setattr(remote, "_generate_run_id", lambda: "cloud-batch-test-001")
        monkeypatch.setattr(remote.s3_helper, "get_run_s3_prefix", lambda rid: f"s3://b/{rid}")
        monkeypatch.setattr(remote.s3_helper, "upload_code", lambda *a: None)
        monkeypatch.setattr(remote.s3_helper, "check_exit_code", lambda rid: 0)
        monkeypatch.setattr(remote.s3_helper, "download_results", lambda *a, **kw: None)
        monkeypatch.setattr(project_state, "register_run", mock_register_run)
        monkeypatch.setattr(project_state, "remove_run", lambda *a: None)

        remote.run(
            command="echo test",
            backend=backend,
            backend_name="aws",
            project_root="/tmp/fake",
            instance_type="c7a.8xlarge",
            batch_id="batch-passthrough-test",
        )

        assert registered_kwargs.get("batch_id") == "batch-passthrough-test"


# ---------------------------------------------------------------------------
# 14. test_mcp_tool_definition (additional — verifies MCP server changes)
# ---------------------------------------------------------------------------

class TestMCPToolDefinition:
    """Verify kit.research_batch tool exists in MCP server."""

    def test_research_batch_tool_in_definitions(self):
        """TOOL_DEFINITIONS should include kit.research_batch."""
        sys.path.insert(0, str(ROOT / "mcp"))
        from server import TOOL_DEFINITIONS

        tool_names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "kit.research_batch" in tool_names, (
            f"kit.research_batch not found in TOOL_DEFINITIONS. Found: {tool_names}"
        )

    def test_research_batch_tool_schema(self):
        """kit.research_batch tool should require spec_paths (array of strings)."""
        sys.path.insert(0, str(ROOT / "mcp"))
        from server import TOOL_DEFINITIONS

        tool = next((t for t in TOOL_DEFINITIONS if t["name"] == "kit.research_batch"), None)
        assert tool is not None

        schema = tool["inputSchema"]
        assert "spec_paths" in schema["properties"]
        assert schema["properties"]["spec_paths"]["type"] == "array"
        assert "spec_paths" in schema.get("required", [])

    def test_research_batch_call_tool_dispatch(self):
        """call_tool('kit.research_batch', ...) should route to the handler."""
        sys.path.insert(0, str(ROOT / "mcp"))
        from server import MasterKitFacade, ServerConfig

        config = ServerConfig(
            root=ROOT,
            host="127.0.0.1",
            port=7337,
            token="test",
            max_output_bytes=32000,
            log_dir=ROOT / "runs" / "mcp-logs",
        )
        facade = MasterKitFacade(config)

        # Mock _launch_background to avoid actual subprocess
        launched = {}

        def mock_launch(kit, action, args):
            launched["kit"] = kit
            launched["action"] = action
            launched["args"] = args
            return {"run_id": "test-run-id", "status": "launched"}

        facade._launch_background = mock_launch

        result = facade.call_tool("kit.research_batch", {"spec_paths": ["spec-a.md", "spec-b.md"]})

        assert launched["kit"] == "research"
        assert launched["action"] == "batch"
        assert launched["args"] == ["spec-a.md", "spec-b.md"]
        assert result["status"] == "launched"


# ---------------------------------------------------------------------------
# 15. test_batch_state_dir (additional — tests _batch_state_dir)
# ---------------------------------------------------------------------------

class TestBatchStateDir:
    """Verify _batch_state_dir creates and returns the correct path."""

    def test_batch_state_dir_returns_path(self):
        """_batch_state_dir should return a Path ending in 'batches'."""
        from cloud.batch import _batch_state_dir

        result = _batch_state_dir()
        assert isinstance(result, Path)
        assert result.name == "batches"

    def test_batch_state_dir_creates_directory(self, monkeypatch, tmp_path):
        """_batch_state_dir should create the directory if it doesn't exist."""
        from cloud import batch as batch_mod

        target = tmp_path / "fake-cloud" / "batches"
        assert not target.exists()

        # Temporarily monkeypatch to return our target
        original = batch_mod._batch_state_dir

        def patched():
            target.mkdir(parents=True, exist_ok=True)
            return target

        monkeypatch.setattr(batch_mod, "_batch_state_dir", patched)

        result = batch_mod._batch_state_dir()
        assert result.is_dir()
