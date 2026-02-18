"""S3 upload/download helpers for cloud execution."""

from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

from .config import S3_BUCKET, S3_RUNS_PREFIX, AWS_REGION


def _s3_prefix(run_id: str) -> str:
    return f"s3://{S3_BUCKET}/{S3_RUNS_PREFIX}/{run_id}"


def upload_code(project_root: str, run_id: str, *, exclude_patterns: Optional[list[str]] = None) -> str:
    """Tar project code (respecting .gitignore) and upload to S3.

    Returns the S3 URI of the uploaded archive.
    """
    s3_uri = f"{_s3_prefix(run_id)}/code.tar.gz"
    project = Path(project_root)

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # Use git ls-files to respect .gitignore, fall back to full dir
        tracked_files = _git_tracked_files(project)
        if tracked_files is not None:
            _create_tar_from_list(project, tracked_files, tmp_path)
        else:
            _create_tar_from_dir(project, tmp_path, exclude_patterns or [])

        _aws_s3_cp(tmp_path, s3_uri)
    finally:
        os.unlink(tmp_path)

    return s3_uri


def upload_dirs(local_dirs: list[str], run_id: str) -> list[str]:
    """Upload local directories to S3 under the run prefix.

    Returns list of S3 URIs.
    """
    uris = []
    for local_dir in local_dirs:
        p = Path(local_dir)
        if not p.is_dir():
            raise FileNotFoundError(f"Data directory not found: {local_dir}")
        name = p.name
        s3_uri = f"{_s3_prefix(run_id)}/data/{name}/"
        _aws_s3_sync(str(p), s3_uri)
        uris.append(s3_uri)
    return uris


def download_results(run_id: str, local_dir: str, remote_subdir: str = "results") -> None:
    """Download results from S3 to a local directory."""
    s3_uri = f"{_s3_prefix(run_id)}/{remote_subdir}/"
    Path(local_dir).mkdir(parents=True, exist_ok=True)
    _aws_s3_sync(s3_uri, local_dir)


def check_exit_code(run_id: str) -> Optional[int]:
    """Check if the remote run has written an exit_code marker to S3.

    Returns the exit code (int) if found, None if not yet written.
    """
    s3_uri = f"{_s3_prefix(run_id)}/exit_code"
    try:
        result = subprocess.run(
            ["aws", "s3", "cp", s3_uri, "-", "--region", AWS_REGION],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError):
        pass
    return None


def write_marker(run_id: str, key: str, content: str) -> None:
    """Write a small marker file to S3 (e.g., exit_code, started)."""
    s3_uri = f"{_s3_prefix(run_id)}/{key}"
    subprocess.run(
        ["aws", "s3", "cp", "-", s3_uri, "--region", AWS_REGION],
        input=content, capture_output=True, text=True, timeout=15,
    )


def get_run_s3_prefix(run_id: str) -> str:
    """Return the full S3 prefix for a run."""
    return _s3_prefix(run_id)


def cleanup(run_id: str) -> None:
    """Remove all S3 objects for a run."""
    s3_uri = f"{_s3_prefix(run_id)}/"
    subprocess.run(
        ["aws", "s3", "rm", "--recursive", s3_uri, "--region", AWS_REGION],
        capture_output=True, timeout=120,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _git_tracked_files(project: Path) -> Optional[list[str]]:
    """Return list of git-tracked files, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=str(project), timeout=30,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().splitlines() if f]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _create_tar_from_list(root: Path, files: list[str], output: str) -> None:
    """Create a tar.gz from a list of relative file paths."""
    with tarfile.open(output, "w:gz") as tar:
        for f in files:
            full = root / f
            if full.is_file():
                tar.add(str(full), arcname=f)


def _create_tar_from_dir(root: Path, output: str, exclude: list[str]) -> None:
    """Create a tar.gz from a directory, excluding patterns."""
    def _filter(info: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        for pat in exclude:
            if pat in info.name:
                return None
        # Skip common large/irrelevant dirs
        for skip in [".git/", "__pycache__/", "node_modules/", ".venv/"]:
            if skip in info.name:
                return None
        return info

    with tarfile.open(output, "w:gz") as tar:
        tar.add(str(root), arcname=".", filter=_filter)


def _aws_s3_cp(local_path: str, s3_uri: str) -> None:
    """Copy a local file to S3."""
    result = subprocess.run(
        ["aws", "s3", "cp", local_path, s3_uri, "--region", AWS_REGION],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"S3 upload failed: {result.stderr}")


def _aws_s3_sync(src: str, dst: str) -> None:
    """Sync between local and S3 (either direction)."""
    result = subprocess.run(
        ["aws", "s3", "sync", src, dst, "--region", AWS_REGION],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"S3 sync failed: {result.stderr}")
