"""All API payload builders."""
from __future__ import annotations

import base64
import datetime as dt
import filecmp
import json
import os
import re
import signal
import socket
import sqlite3
from pathlib import Path
from typing import Any

from .config import db_path
from .schema import ensure_schema


def load_dashboard_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def load_one_row(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    row = conn.execute(query, params).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def summary_payload(project_id: str | None) -> dict[str, Any]:
    params: list[Any] = []
    project_clause = ""
    if project_id:
        project_clause = "WHERE project_id = ?"
        params.append(project_id)

    runs = load_one_row(
        f"""
        SELECT
          COUNT(*) AS total_runs,
          SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_runs,
          SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_runs,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_runs
        FROM runs
        {project_clause}
        """,
        tuple(params),
    ) or {}

    reqs = load_one_row(
        f"""
        SELECT
          COUNT(*) AS total_requests,
          SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_requests,
          SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_requests,
          SUM(CASE WHEN status = 'failed' OR status IS NULL THEN 1 ELSE 0 END) AS failed_requests
        FROM requests
        {project_clause}
        """,
        tuple(params),
    ) or {}

    active_by_phase = load_dashboard_rows(
        f"""
        SELECT
          COALESCE(kit, 'unknown') AS kit,
          COALESCE(phase, 'unknown') AS phase,
          COUNT(*) AS count
        FROM runs
        {project_clause if project_clause else ''}
        {('AND' if project_clause else 'WHERE')} status = 'running'
        GROUP BY COALESCE(kit, 'unknown'), COALESCE(phase, 'unknown')
        ORDER BY count DESC, kit ASC, phase ASC
        """,
        tuple(params),
    )

    return {
        "runs": runs,
        "requests": reqs,
        "active_by_phase": active_by_phase,
    }


def graph_payload(project_id: str | None) -> dict[str, Any]:
    where = ""
    params: tuple[Any, ...] = ()
    if project_id:
        where = "WHERE project_id = ?"
        params = (project_id,)

    edges = load_dashboard_rows(
        f"""
        SELECT
          COALESCE(from_kit, 'unknown') AS from_kit,
          COALESCE(from_phase, '?') AS from_phase,
          COALESCE(to_kit, 'unknown') AS to_kit,
          COALESCE(to_phase, '?') AS to_phase,
          COUNT(*) AS total,
          SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok,
          SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked,
          SUM(CASE WHEN status = 'failed' OR status IS NULL THEN 1 ELSE 0 END) AS failed
        FROM requests
        {where}
        GROUP BY
          COALESCE(from_kit, 'unknown'),
          COALESCE(from_phase, '?'),
          COALESCE(to_kit, 'unknown'),
          COALESCE(to_phase, '?')
        ORDER BY total DESC, from_kit ASC, to_kit ASC
        """,
        params,
    )

    return {"edges": edges}


def parse_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def list_runs_payload(query: dict[str, str]) -> dict[str, Any]:
    project_id = query.get("project_id")
    status = query.get("status")
    kit = query.get("kit")
    phase = query.get("phase")
    limit = parse_int(query.get("limit"), 200, 1, 2000)
    offset = parse_int(query.get("offset"), 0, 0, 100000)

    # Sort validation
    sort_raw = query.get("sort") or "-started_at"
    allowed_sort_cols = {"started_at", "status", "kit", "phase", "run_id", "experiment_name"}
    desc = sort_raw.startswith("-")
    sort_col = sort_raw.lstrip("-")
    if sort_col not in allowed_sort_cols:
        sort_col = "started_at"
        desc = True
    order_dir = "DESC" if desc else "ASC"

    clauses: list[str] = []
    params: list[Any] = []

    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if kit:
        clauses.append("kit = ?")
        params.append(kit)
    if phase:
        clauses.append("phase = ?")
        params.append(phase)

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    rows = load_dashboard_rows(
        f"""
        SELECT
          project_id,
          run_id,
          parent_run_id,
          kit,
          phase,
          status,
          started_at,
          finished_at,
          exit_code,
          cwd,
          project_root,
          orchestration_kit_root,
          agent_runtime,
          host,
          pid,
          capsule_path,
          manifest_path,
          log_path,
          events_path,
          reasoning,
          experiment_name,
          verdict
        FROM runs
        {where}
        ORDER BY COALESCE({sort_col}, '') {order_dir}, run_id DESC
        LIMIT ? OFFSET ?
        """,
        tuple([*params, limit, offset]),
    )

    # Post-process: add duration_seconds, is_stale, and is_orphaned
    now = dt.datetime.now(dt.timezone.utc)
    local_hostname = socket.gethostname()
    for row in rows:
        started = row.get("started_at")
        finished = row.get("finished_at")
        duration = None
        if started:
            try:
                start_dt = dt.datetime.fromisoformat(started.replace("Z", "+00:00"))
                if finished:
                    end_dt = dt.datetime.fromisoformat(finished.replace("Z", "+00:00"))
                else:
                    end_dt = now
                duration = max(0, int((end_dt - start_dt).total_seconds()))
            except (ValueError, TypeError):
                pass
        row["duration_seconds"] = duration
        row["is_stale"] = row.get("status") == "running" and duration is not None and duration > 1800

        # Orphan detection: for local "running" runs, check if the PID is still alive.
        is_orphaned = False
        if row.get("status") == "running" and row.get("pid") and row.get("host") == local_hostname:
            try:
                os.kill(int(row["pid"]), 0)
            except ProcessLookupError:
                is_orphaned = True
            except (PermissionError, OSError, TypeError, ValueError):
                pass  # process exists but we can't signal it, or bad pid
        row["is_orphaned"] = is_orphaned

    has_more = len(rows) == limit
    return {"runs": rows, "limit": limit, "offset": offset, "has_more": has_more}


def _parent_map(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    return {str(row["run_id"]): row.get("parent_run_id") for row in rows if row.get("run_id") is not None}


def _root_for_run(run_id: str, parents: dict[str, str | None]) -> str:
    current = run_id
    seen: set[str] = set()
    while True:
        if current in seen:
            return run_id
        seen.add(current)
        parent = parents.get(current)
        if not isinstance(parent, str) or parent not in parents:
            return current
        current = parent


def run_detail_payload(project_id: str, run_id: str) -> dict[str, Any]:
    run = load_one_row(
        """
        SELECT *
        FROM runs
        WHERE project_id = ? AND run_id = ?
        """,
        (project_id, run_id),
    )
    if run is None:
        raise KeyError("run not found")

    all_runs = load_dashboard_rows(
        """
        SELECT *
        FROM runs
        WHERE project_id = ?
        """,
        (project_id,),
    )
    parents = _parent_map(all_runs)
    root = _root_for_run(run_id, parents)

    thread_runs = [
        item
        for item in all_runs
        if _root_for_run(str(item["run_id"]), parents) == root
    ]
    thread_runs.sort(key=lambda x: ((x.get("started_at") or ""), str(x.get("run_id") or "")))
    thread_run_ids = {str(item["run_id"]) for item in thread_runs if item.get("run_id")}

    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in thread_run_ids) or "''"
    params: list[Any] = [project_id]
    params.extend(sorted(thread_run_ids))
    params.extend(sorted(thread_run_ids))
    request_rows = conn.execute(
        f"""
        SELECT *
        FROM requests
        WHERE project_id = ?
          AND (
            parent_run_id IN ({placeholders})
            OR child_run_id IN ({placeholders})
          )
        ORDER BY COALESCE(enqueued_ts, completed_ts, '') ASC, request_id ASC
        """,
        tuple(params),
    ).fetchall()
    conn.close()

    requests = [dict(row) for row in request_rows]

    return {
        "run": run,
        "thread_root_run_id": root,
        "thread_runs": thread_runs,
        "thread_requests": requests,
    }


def _project_row(project_id: str) -> dict[str, Any]:
    row = load_one_row(
        "SELECT project_id, orchestration_kit_root, project_root FROM projects WHERE project_id = ?",
        (project_id,),
    )
    if row is None:
        raise KeyError("project not found")
    return row


def _resolve_artifact_path(
    *,
    project_id: str,
    raw_path: str,
    scope: str = "auto",
) -> tuple[Path, Path]:
    project = _project_row(project_id)
    orchestration_kit_root = Path(str(project["orchestration_kit_root"])).expanduser().resolve()
    project_root = Path(str(project["project_root"])).expanduser().resolve()

    if scope not in {"auto", "orchestration-kit", "project"}:
        raise ValueError("invalid scope; expected auto|orchestration-kit|project")

    if scope == "orchestration-kit":
        roots: list[tuple[str, Path]] = [("orchestration-kit", orchestration_kit_root)]
    elif scope == "project":
        roots = [("project", project_root)]
    else:
        roots = [("orchestration-kit", orchestration_kit_root), ("project", project_root)]

    path_raw = str(raw_path).strip()
    path_obj = Path(path_raw)

    candidates_by_scope: dict[str, list[Path]] = {}
    for scope_name, root in roots:
        candidates: list[Path] = []
        if path_obj.is_absolute():
            candidates.append(path_obj.resolve())
        else:
            normalized = path_raw.lstrip("/")
            variants = [normalized]
            if normalized.startswith("orchestration-kit/"):
                variants.append(normalized[len("orchestration-kit/"):])
            if normalized.startswith("project/"):
                variants.append(normalized[len("project/"):])
            seen: set[str] = set()
            for v in variants:
                if v in seen:
                    continue
                seen.add(v)
                candidates.append((root / v).resolve())
        candidates_by_scope[scope_name] = candidates

    for scope_name, root in roots:
        for candidate in candidates_by_scope.get(scope_name, []):
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file():
                return root, candidate

    raise FileNotFoundError("artifact not found")


def _kind_for_artifact(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".json":
        return "json"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix in {".log", ".txt"}:
        return "text"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg"}:
        return "image"
    if suffix == ".csv":
        return "csv"
    return "text"


_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


def artifact_payload(
    *,
    project_id: str,
    raw_path: str,
    max_bytes: int | None = None,
    scope: str = "auto",
) -> dict[str, Any]:
    max_cap = int(os.getenv("ORCHESTRATION_KIT_DASHBOARD_ARTIFACT_MAX_BYTES", "240000"))
    if max_bytes is None:
        max_bytes = max_cap
    max_bytes = max(1024, min(int(max_bytes), max_cap))

    root, resolved = _resolve_artifact_path(project_id=project_id, raw_path=raw_path, scope=scope)
    size = resolved.stat().st_size
    kind = _kind_for_artifact(resolved)

    # Image: binary read + base64
    if kind == "image":
        image_max = min(5 * 1024 * 1024, max_bytes)
        to_read = min(size, image_max)
        with resolved.open("rb") as fh:
            raw = fh.read(to_read)
        rel_path = str(resolved.relative_to(root))
        mime = _IMAGE_MIME.get(resolved.suffix.lower(), "application/octet-stream")
        return {
            "project_id": project_id,
            "path": rel_path,
            "kind": "image",
            "mime": mime,
            "data_base64": base64.b64encode(raw).decode("ascii"),
            "bytes_total": size,
            "bytes_read": to_read,
            "truncated": size > to_read,
        }

    to_read = min(size, max_bytes)
    with resolved.open("rb") as fh:
        raw = fh.read(to_read)
    text = raw.decode("utf-8", errors="replace")
    truncated = size > to_read

    if kind in {"json", "jsonl"}:
        if resolved.suffix.lower() == ".jsonl":
            rows: list[str] = []
            for ln in text.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    payload = json.loads(ln)
                    rows.append(json.dumps(payload, indent=2, sort_keys=True))
                except Exception:
                    rows.append(ln)
            text = "\n".join(rows)
        else:
            try:
                payload = json.loads(text)
                text = json.dumps(payload, indent=2, sort_keys=True)
            except Exception:
                pass

    # Strip ANSI escape codes from text/log/markdown for clean display
    if kind in {"text", "markdown"}:
        text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)

    rel_path = str(resolved.relative_to(root))
    return {
        "project_id": project_id,
        "path": rel_path,
        "kind": kind,
        "bytes_total": size,
        "bytes_read": to_read,
        "truncated": truncated,
        "text": text,
    }


def project_docs_payload(project_id: str) -> dict[str, Any]:
    project = _project_row(project_id)
    project_root = Path(str(project["project_root"])).expanduser().resolve()
    orchestration_kit_root = Path(str(project["orchestration_kit_root"])).expanduser().resolve()

    # Detect greenfield mode: state files live under .kit/ instead of project root
    kit_state_dir = project_root / ".kit"
    is_greenfield = kit_state_dir.is_dir()
    kit_prefix = ".kit/" if is_greenfield else ""

    fixed_project_docs = [
        f"{kit_prefix}LAST_TOUCH.md",
        f"{kit_prefix}DOMAIN_PRIORS.md",
        f"{kit_prefix}CONSTRUCTION_LOG.md",
        f"{kit_prefix}CONSTRUCTIONS.md",
        f"{kit_prefix}DOMAIN_CONTEXT.md",
        f"{kit_prefix}RESEARCH_LOG.md",
        f"{kit_prefix}QUESTIONS.md",
        f"{kit_prefix}PRD.md",
        "CLAUDE.md",
        "README.md",
    ]
    fixed_master_docs = [
        "tdd-kit/LAST_TOUCH.md",
        "research-kit/DOMAIN_PRIORS.md",
        "research-kit/RESEARCH_LOG.md",
        "research-kit/QUESTIONS.md",
        "mathematics-kit/CONSTRUCTION_LOG.md",
        "mathematics-kit/CONSTRUCTIONS.md",
        "mathematics-kit/DOMAIN_CONTEXT.md",
    ]

    kit_template_dirs = [
        orchestration_kit_root / "tdd-kit" / "templates",
        orchestration_kit_root / "research-kit" / "templates",
        orchestration_kit_root / "mathematics-kit" / "templates",
    ]

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _matches_template(file_path: Path) -> bool:
        fname = file_path.name
        for tpl_dir in kit_template_dirs:
            tpl = tpl_dir / fname
            if tpl.is_file():
                try:
                    if filecmp.cmp(str(file_path), str(tpl), shallow=False):
                        return True
                except OSError:
                    continue
        return False

    def add_entry(scope_name: str, rel_path: str, *, required: bool) -> None:
        key = (scope_name, rel_path)
        if key in seen:
            return
        seen.add(key)

        base = project_root if scope_name == "project" else orchestration_kit_root
        candidate = (base / rel_path).resolve()
        if candidate.exists():
            try:
                candidate.relative_to(base)
            except ValueError:
                return
        exists = candidate.is_file()
        item: dict[str, Any] = {
            "scope": scope_name,
            "path": rel_path,
            "name": candidate.name,
            "required": required,
            "exists": exists,
        }
        if exists:
            stat = candidate.stat()
            item["bytes"] = int(stat.st_size)
            item["modified_at"] = dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            item["populated"] = not _matches_template(candidate)
        entries.append(item)

    for rel in fixed_project_docs:
        add_entry("project", rel, required=True)

    # In greenfield mode, .kit/ files supersede orchestration-kit templates
    if not is_greenfield:
        for rel in fixed_master_docs:
            add_entry("orchestration-kit", rel, required=False)

    scan_roots = [project_root]
    if is_greenfield:
        scan_roots.append(kit_state_dir)
    for scan_root in scan_roots:
        for folder in ("docs", "specs", "experiments"):
            base = scan_root / folder
            if not base.is_dir():
                continue
            for p in sorted(base.glob("*.md"))[:80]:
                rel = str(p.relative_to(project_root))
                add_entry("project", rel, required=False)

    entries.sort(
        key=lambda x: (
            0 if x.get("scope") == "project" else 1,
            0 if x.get("required") else 1,
            str(x.get("name", "")).lower(),
            str(x.get("path", "")).lower(),
        )
    )
    return {
        "project_id": project_id,
        "docs": entries,
    }


def capsule_preview_payload(project_id: str, run_id: str) -> dict[str, Any]:
    run = load_one_row(
        "SELECT capsule_path, manifest_path, log_path, events_path FROM runs WHERE project_id = ? AND run_id = ?",
        (project_id, run_id),
    )
    if run is None:
        raise KeyError("run not found")

    result: dict[str, Any] = {"project_id": project_id, "run_id": run_id}

    for key in ("capsule_path", "manifest_path"):
        path = run.get(key)
        if path:
            try:
                payload = artifact_payload(project_id=project_id, raw_path=path, max_bytes=60000)
                result[key.replace("_path", "")] = payload
            except (FileNotFoundError, ValueError):
                result[key.replace("_path", "")] = None
        else:
            result[key.replace("_path", "")] = None

    result["artifact_paths"] = {
        "capsule": run.get("capsule_path"),
        "manifest": run.get("manifest_path"),
        "log": run.get("log_path"),
        "events": run.get("events_path"),
    }

    # Extract result artifacts from manifest's artifact_index.tracked[]
    result_artifacts: list[dict[str, Any]] = []
    manifest_path = run.get("manifest_path")
    if manifest_path:
        try:
            manifest_payload = artifact_payload(project_id=project_id, raw_path=manifest_path, max_bytes=120000)
            manifest_text = manifest_payload.get("text", "")
            manifest_data = json.loads(manifest_text) if manifest_text else {}
            ai = manifest_data.get("artifact_index")
            if isinstance(ai, dict):
                tracked = ai.get("tracked")
                if isinstance(tracked, list):
                    for art in tracked:
                        if not isinstance(art, dict):
                            continue
                        art_path = art.get("path", "")
                        if isinstance(art_path, str) and "/results/" in art_path:
                            result_artifacts.append({
                                "path": art_path,
                                "size": art.get("size"),
                                "kind": art.get("kind"),
                            })
        except Exception:
            pass
    result["result_artifacts"] = result_artifacts

    return result
