"""Project registry CRUD operations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import now_iso, registry_path, project_id_for


def load_registry() -> list[dict[str, Any]]:
    path = registry_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    projects: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        project_id = item.get("project_id")
        orchestration_kit_root = item.get("orchestration_kit_root")
        project_root = item.get("project_root")
        label = item.get("label")
        if not all(isinstance(x, str) and x for x in (project_id, orchestration_kit_root, project_root, label)):
            continue
        projects.append(
            {
                "project_id": project_id,
                "label": label,
                "orchestration_kit_root": orchestration_kit_root,
                "project_root": project_root,
                "registered_at": item.get("registered_at") if isinstance(item.get("registered_at"), str) else None,
                "updated_at": item.get("updated_at") if isinstance(item.get("updated_at"), str) else None,
            }
        )
    return projects


def save_registry(projects: list[dict[str, Any]]) -> None:
    path = registry_path()
    path.write_text(json.dumps(projects, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def upsert_registry_project(*, orchestration_kit_root: Path, project_root: Path, label: str | None) -> dict[str, Any]:
    projects = load_registry()
    orchestration_kit_root = orchestration_kit_root.resolve()
    project_root = project_root.resolve()

    project_id = project_id_for(orchestration_kit_root)
    now = now_iso()
    resolved_label = label.strip() if isinstance(label, str) and label.strip() else project_root.name

    updated: list[dict[str, Any]] = []
    record: dict[str, Any] | None = None
    for item in projects:
        if item.get("project_id") == project_id:
            existing_registered = item.get("registered_at") if isinstance(item.get("registered_at"), str) else now
            record = {
                "project_id": project_id,
                "label": resolved_label,
                "orchestration_kit_root": str(orchestration_kit_root),
                "project_root": str(project_root),
                "registered_at": existing_registered,
                "updated_at": now,
            }
            updated.append(record)
        else:
            updated.append(item)

    if record is None:
        record = {
            "project_id": project_id,
            "label": resolved_label,
            "orchestration_kit_root": str(orchestration_kit_root),
            "project_root": str(project_root),
            "registered_at": now,
            "updated_at": now,
        }
        updated.append(record)

    updated.sort(key=lambda x: str(x.get("label", "")).lower())
    save_registry(updated)
    return record


def remove_registry_project(project_id: str) -> bool:
    projects = load_registry()
    filtered = [item for item in projects if item.get("project_id") != project_id]
    if len(filtered) == len(projects):
        return False
    save_registry(filtered)
    return True
