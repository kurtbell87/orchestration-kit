"""Service management helpers (ensure, stop, status)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .config import service_state_path, service_log_path


def load_service_state() -> dict[str, Any]:
    path = service_state_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def save_service_state(payload: dict[str, Any]) -> None:
    path = service_state_path()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def healthcheck(*, host: str, port: int, timeout: float = 0.6) -> bool:
    req = urllib.request.Request(f"http://{host}:{port}/health", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False
