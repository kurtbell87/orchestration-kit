"""S3-backed artifact store with content-addressed storage and symlink proxy.

Files are stored in S3 at:
    s3://<bucket>/artifact-store/<sha256-prefix-2>/<sha256>.<ext>

Local manifests (.s3-manifest.json) track which files belong to each experiment.
After hydration, local paths become symlinks to a cache directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .config import S3_BUCKET, AWS_REGION

S3_ARTIFACT_PREFIX = "artifact-store"
MANIFEST_FILENAME = ".s3-manifest.json"
MANIFEST_VERSION = 1


def _s3_key(sha256: str, ext: str) -> str:
    """Content-addressed S3 key: artifact-store/<2-char prefix>/<sha256>.<ext>"""
    return f"{S3_ARTIFACT_PREFIX}/{sha256[:2]}/{sha256}{ext}"


def _s3_uri(key: str) -> str:
    return f"s3://{S3_BUCKET}/{key}"


def _cache_dir(project_root: Path) -> Path:
    return project_root / ".kit" / ".s3-cache"


def _cache_path(project_root: Path, sha256: str, ext: str) -> Path:
    return _cache_dir(project_root) / f"{sha256}{ext}"


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)  # 1 MB
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _s3_head(s3_key: str) -> bool:
    """Check if an S3 object exists (HEAD)."""
    result = subprocess.run(
        ["aws", "s3api", "head-object",
         "--bucket", S3_BUCKET,
         "--key", s3_key,
         "--region", AWS_REGION],
        capture_output=True, timeout=30,
    )
    return result.returncode == 0


def _s3_upload(local_path: str, s3_key: str) -> None:
    """Upload a local file to S3."""
    result = subprocess.run(
        ["aws", "s3", "cp", local_path, _s3_uri(s3_key), "--region", AWS_REGION],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"S3 upload failed: {result.stderr}")


def _s3_download(s3_key: str, local_path: str) -> None:
    """Download an S3 object to a local file."""
    result = subprocess.run(
        ["aws", "s3", "cp", _s3_uri(s3_key), local_path, "--region", AWS_REGION],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"S3 download failed: {result.stderr}")


def load_manifest(manifest_path: Path) -> dict:
    """Load a manifest file, returning empty structure if missing."""
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    return {"version": MANIFEST_VERSION, "files": {}}


def save_manifest(manifest_path: Path, manifest: dict) -> None:
    """Write manifest to disk (sorted keys for stable diffs)."""
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")


def push_file(
    file_path: Path,
    project_root: Path,
    manifest_dir: Optional[Path] = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Hash a file, upload to S3, replace with symlink, update manifest.

    Returns dict with sha256, s3_key, size, skipped (bool).
    """
    file_path = file_path.resolve()
    project_root = project_root.resolve()

    if not file_path.is_file():
        raise FileNotFoundError(f"Not a file: {file_path}")
    if file_path.is_symlink():
        raise ValueError(f"Already a symlink: {file_path}")

    ext = file_path.suffix  # e.g., ".csv"
    size = file_path.stat().st_size
    digest = sha256_file(file_path)
    s3_key = _s3_key(digest, ext)

    info = {
        "sha256": digest,
        "size": size,
        "s3_key": s3_key,
        "original_path": str(file_path.relative_to(project_root)),
        "skipped_upload": False,
    }

    if dry_run:
        info["dry_run"] = True
        return info

    # Upload (skip if identical content already in S3)
    if _s3_head(s3_key):
        info["skipped_upload"] = True
    else:
        _s3_upload(str(file_path), s3_key)

    # Copy to local cache
    cache = _cache_path(project_root, digest, ext)
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        # Move original to cache (avoids double disk usage)
        file_path.rename(cache)
    else:
        # Cache already has it — just remove the original
        file_path.unlink()

    # Create symlink from original location to cache
    rel = os.path.relpath(cache, file_path.parent)
    file_path.symlink_to(rel)

    # Update manifest
    if manifest_dir is None:
        manifest_dir = file_path.parent
    manifest_path = manifest_dir / MANIFEST_FILENAME
    manifest = load_manifest(manifest_path)
    manifest["version"] = MANIFEST_VERSION
    filename = file_path.name
    manifest["files"][filename] = {
        "sha256": digest,
        "size": size,
        "s3_key": s3_key,
        "original_path": info["original_path"],
    }
    save_manifest(manifest_path, manifest)

    return info


def push_dir(
    dir_path: Path,
    project_root: Path,
    *,
    threshold_bytes: int = 10 * 1024 * 1024,
    dry_run: bool = False,
) -> list[dict]:
    """Push all files above threshold in a directory tree.

    Walks subdirectories. Each subdirectory with pushed files gets its own manifest.
    Returns list of push results.
    """
    dir_path = dir_path.resolve()
    results = []

    for root, _dirs, files in os.walk(dir_path):
        root_path = Path(root)
        for name in sorted(files):
            fp = root_path / name
            if fp.is_symlink():
                continue
            if name == MANIFEST_FILENAME:
                continue
            if fp.stat().st_size < threshold_bytes:
                continue
            try:
                info = push_file(fp, project_root, manifest_dir=root_path, dry_run=dry_run)
                info["file"] = str(fp.relative_to(project_root))
                results.append(info)
            except Exception as e:
                results.append({"file": str(fp), "error": str(e)})

    return results


def hydrate(
    dir_path: Path,
    project_root: Path,
) -> list[dict]:
    """Download S3 artifacts to local cache, create symlinks.

    Walks dir_path for .s3-manifest.json files and hydrates each.
    Returns list of results per file.
    """
    dir_path = dir_path.resolve()
    project_root = project_root.resolve()
    results = []

    for root, _dirs, files in os.walk(dir_path):
        if MANIFEST_FILENAME not in files:
            continue
        manifest_path = Path(root) / MANIFEST_FILENAME
        manifest = load_manifest(manifest_path)

        for filename, entry in manifest.get("files", {}).items():
            file_path = Path(root) / filename
            digest = entry["sha256"]
            ext = Path(filename).suffix
            s3_key = entry["s3_key"]
            cache = _cache_path(project_root, digest, ext)

            status = {"file": str(file_path.relative_to(project_root))}

            # If real file already exists (not symlink), skip
            if file_path.exists() and not file_path.is_symlink():
                status["status"] = "exists_real"
                results.append(status)
                continue

            # Download to cache if needed
            if not cache.exists():
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    _s3_download(s3_key, str(cache))
                    status["downloaded"] = True
                except Exception as e:
                    status["status"] = "download_failed"
                    status["error"] = str(e)
                    results.append(status)
                    continue
            else:
                status["downloaded"] = False

            # Create or fix symlink
            if file_path.is_symlink():
                # Already a symlink — verify it points to the right place
                target = file_path.resolve()
                if target == cache.resolve():
                    status["status"] = "ok"
                else:
                    file_path.unlink()
                    rel = os.path.relpath(cache, file_path.parent)
                    file_path.symlink_to(rel)
                    status["status"] = "fixed_symlink"
            else:
                # Create symlink
                file_path.parent.mkdir(parents=True, exist_ok=True)
                rel = os.path.relpath(cache, file_path.parent)
                file_path.symlink_to(rel)
                status["status"] = "created_symlink"

            results.append(status)

    return results


def status(
    dir_path: Path,
    project_root: Path,
) -> list[dict]:
    """Show which manifest files are cached/missing locally."""
    dir_path = dir_path.resolve()
    project_root = project_root.resolve()
    results = []

    for root, _dirs, files in os.walk(dir_path):
        if MANIFEST_FILENAME not in files:
            continue
        manifest_path = Path(root) / MANIFEST_FILENAME
        manifest = load_manifest(manifest_path)

        for filename, entry in manifest.get("files", {}).items():
            file_path = Path(root) / filename
            digest = entry["sha256"]
            ext = Path(filename).suffix
            cache = _cache_path(project_root, digest, ext)

            rec = {
                "file": str(file_path.relative_to(project_root)),
                "size": entry["size"],
                "sha256": digest[:12] + "...",
            }

            if file_path.exists() and not file_path.is_symlink():
                rec["status"] = "real_file"
            elif file_path.is_symlink() and cache.exists():
                rec["status"] = "cached"
            elif file_path.is_symlink() and not cache.exists():
                rec["status"] = "broken_symlink"
            elif cache.exists():
                rec["status"] = "cached_no_link"
            else:
                rec["status"] = "missing"

            results.append(rec)

    return results


def verify(
    dir_path: Path,
    project_root: Path,
) -> list[dict]:
    """Verify SHA-256 of cached files matches manifest."""
    dir_path = dir_path.resolve()
    project_root = project_root.resolve()
    results = []

    for root, _dirs, files in os.walk(dir_path):
        if MANIFEST_FILENAME not in files:
            continue
        manifest_path = Path(root) / MANIFEST_FILENAME
        manifest = load_manifest(manifest_path)

        for filename, entry in manifest.get("files", {}).items():
            file_path = Path(root) / filename
            digest = entry["sha256"]
            ext = Path(filename).suffix
            cache = _cache_path(project_root, digest, ext)

            rec = {"file": str(file_path.relative_to(project_root))}

            # Find the actual data (follow symlinks)
            actual = file_path.resolve() if file_path.exists() else cache
            if not actual.exists():
                rec["status"] = "missing"
                results.append(rec)
                continue

            actual_hash = sha256_file(actual)
            if actual_hash == digest:
                rec["status"] = "ok"
            else:
                rec["status"] = "MISMATCH"
                rec["expected"] = digest[:12] + "..."
                rec["actual"] = actual_hash[:12] + "..."

            results.append(rec)

    return results
