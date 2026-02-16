"""Paths, env vars, and constants for the dashboard package."""
from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def dashboard_home() -> Path:
    raw = os.getenv("ORCHESTRATION_KIT_DASHBOARD_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".orchestration-kit-dashboard").resolve()


def ensure_dashboard_home() -> Path:
    home = dashboard_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass

    if os.access(str(home), os.W_OK | os.X_OK):
        return home

    fallback = Path("/tmp/orchestration-kit-dashboard").resolve()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def registry_path() -> Path:
    return ensure_dashboard_home() / "projects.json"


def db_path() -> Path:
    return ensure_dashboard_home() / "state.db"


def service_state_path() -> Path:
    return ensure_dashboard_home() / "service.json"


def service_log_path() -> Path:
    return ensure_dashboard_home() / "service.log"


def rel_to(base: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def coerce_path(raw: str | None, fallback: Path) -> Path:
    if raw is None:
        return fallback.resolve()
    return Path(raw).expanduser().resolve()


def current_orchestration_kit_root() -> Path:
    env_root = os.getenv("ORCHESTRATION_KIT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return REPO_ROOT


def current_project_root(default_orchestration_kit_root: Path) -> Path:
    env_root = os.getenv("PROJECT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return default_orchestration_kit_root


def project_id_for(orchestration_kit_root: Path) -> str:
    digest = hashlib.sha1(str(orchestration_kit_root).encode("utf-8")).hexdigest()
    return digest[:12]
