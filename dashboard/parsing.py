"""Event and manifest parsing for run data."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import rel_to


def parse_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def resolve_pointer(base: Path, raw: str | None) -> Path | None:
    if not raw or not isinstance(raw, str):
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def parse_manifest_metadata(orchestration_kit_root: Path, manifest_path: str | None) -> dict[str, Any]:
    resolved = resolve_pointer(orchestration_kit_root, manifest_path)
    if resolved is None or not resolved.is_file():
        return {}
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    return metadata


def parse_manifest_full(orchestration_kit_root: Path, manifest_path: str | None) -> dict[str, Any]:
    resolved = resolve_pointer(orchestration_kit_root, manifest_path)
    if resolved is None or not resolved.is_file():
        return {}
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _extract_experiment_name(metadata: dict[str, Any]) -> str | None:
    command = metadata.get("command")
    if not isinstance(command, list) or not command:
        return None
    last_arg = str(command[-1])
    return Path(last_arg).stem or None


_VERDICT_RE = re.compile(r"##\s*Verdict:\s*(CONFIRMED|REFUTED|INCONCLUSIVE)", re.IGNORECASE)


def _extract_verdict(project_root: Path, tracked_artifacts: list[dict[str, Any]]) -> str | None:
    for art in tracked_artifacts:
        art_path = art.get("path", "")
        if not isinstance(art_path, str):
            continue
        if "/results/" not in art_path or not art_path.endswith("/analysis.md"):
            continue
        full = project_root / art_path
        if not full.is_file():
            continue
        try:
            text = full.read_bytes()[:5120].decode("utf-8", errors="replace")
        except OSError:
            continue
        m = _VERDICT_RE.search(text)
        if m:
            return m.group(1).upper()
    return None


def parse_run(
    *,
    project: dict[str, Any],
    run_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events_path = run_root / "events.jsonl"
    records = parse_jsonl(events_path)

    run_id = run_root.name
    run: dict[str, Any] = {
        "project_id": project["project_id"],
        "run_id": run_id,
        "parent_run_id": None,
        "kit": None,
        "phase": None,
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "status": None,
        "capsule_path": None,
        "manifest_path": None,
        "log_path": None,
        "events_path": rel_to(project["orchestration_kit_root_path"], events_path),
        "cwd": None,
        "project_root": project["project_root"],
        "orchestration_kit_root": project["orchestration_kit_root"],
        "agent_runtime": None,
        "host": None,
        "pid": None,
        "reasoning": None,
        "experiment_name": None,
        "verdict": None,
    }

    requests: dict[str, dict[str, Any]] = {}

    for event in records:
        event_name = event.get("event")
        ts = event.get("ts") if isinstance(event.get("ts"), str) else None

        if event_name == "run_started":
            run["run_id"] = str(event.get("run_id", run_id))
            run["parent_run_id"] = event.get("parent_run_id") if isinstance(event.get("parent_run_id"), str) else None
            run["kit"] = event.get("kit") if isinstance(event.get("kit"), str) else run["kit"]
            run["phase"] = event.get("phase") if isinstance(event.get("phase"), str) else run["phase"]
            run["started_at"] = ts or run["started_at"]
            if isinstance(event.get("project_root"), str):
                run["project_root"] = event["project_root"]
            if isinstance(event.get("orchestration_kit_root"), str):
                run["orchestration_kit_root"] = event["orchestration_kit_root"]
            if isinstance(event.get("agent_runtime"), str):
                run["agent_runtime"] = event["agent_runtime"]
            if isinstance(event.get("host"), str):
                run["host"] = event["host"]
            if isinstance(event.get("pid"), int):
                run["pid"] = event["pid"]
            if isinstance(event.get("reasoning"), str):
                run["reasoning"] = event["reasoning"]

        elif event_name == "phase_started":
            if isinstance(event.get("kit"), str):
                run["kit"] = event["kit"]
            if isinstance(event.get("phase"), str):
                run["phase"] = event["phase"]
            if isinstance(event.get("cwd"), str):
                run["cwd"] = event["cwd"]

        elif event_name == "phase_finished":
            if isinstance(event.get("exit_code"), int):
                run["exit_code"] = event["exit_code"]
            if isinstance(event.get("log_path"), str):
                run["log_path"] = event["log_path"]

        elif event_name == "capsule_written":
            if isinstance(event.get("capsule_path"), str):
                run["capsule_path"] = event["capsule_path"]

        elif event_name == "manifest_written":
            if isinstance(event.get("manifest_path"), str):
                run["manifest_path"] = event["manifest_path"]

        elif event_name == "run_finished":
            run["finished_at"] = ts or run["finished_at"]
            if isinstance(event.get("exit_code"), int):
                run["exit_code"] = event["exit_code"]
            if isinstance(event.get("capsule_path"), str):
                run["capsule_path"] = event["capsule_path"]
            if isinstance(event.get("manifest_path"), str):
                run["manifest_path"] = event["manifest_path"]
            if isinstance(event.get("agent_runtime"), str):
                run["agent_runtime"] = event["agent_runtime"]
            if isinstance(event.get("host"), str):
                run["host"] = event["host"]
            if isinstance(event.get("pid"), int):
                run["pid"] = event["pid"]

        elif event_name in {"request_enqueued", "request_completed"}:
            request_id = event.get("request_id")
            if not isinstance(request_id, str):
                continue
            rec = requests.setdefault(
                request_id,
                {
                    "project_id": project["project_id"],
                    "request_id": request_id,
                    "parent_run_id": run_id,
                    "child_run_id": None,
                    "from_kit": None,
                    "from_phase": None,
                    "to_kit": None,
                    "to_phase": None,
                    "action": None,
                    "status": None,
                    "request_path": None,
                    "response_path": None,
                    "enqueued_ts": None,
                    "completed_ts": None,
                    "reasoning": None,
                },
            )
            if isinstance(event.get("request_path"), str):
                rec["request_path"] = event["request_path"]
            if isinstance(event.get("response_path"), str):
                rec["response_path"] = event["response_path"]
            if isinstance(event.get("child_run_id"), str):
                rec["child_run_id"] = event["child_run_id"]
            if isinstance(event.get("from_kit"), str):
                rec["from_kit"] = event["from_kit"]
            if isinstance(event.get("from_phase"), str):
                rec["from_phase"] = event["from_phase"]
            if isinstance(event.get("to_kit"), str):
                rec["to_kit"] = event["to_kit"]
            if isinstance(event.get("to_phase"), str):
                rec["to_phase"] = event["to_phase"]
            if isinstance(event.get("action"), str):
                rec["action"] = event["action"]
            if isinstance(event.get("status"), str):
                rec["status"] = event["status"]
            if isinstance(event.get("reasoning"), str):
                rec["reasoning"] = event["reasoning"]

            if event_name == "request_enqueued":
                rec["enqueued_ts"] = ts or rec["enqueued_ts"]
            else:
                rec["completed_ts"] = ts or rec["completed_ts"]

    if run["manifest_path"] is None:
        manifests = sorted((run_root / "manifests").glob("*.json"))
        if manifests:
            run["manifest_path"] = rel_to(project["orchestration_kit_root_path"], manifests[0])

    if run["capsule_path"] is None:
        capsules = sorted((run_root / "capsules").glob("*.md"))
        if capsules:
            run["capsule_path"] = rel_to(project["orchestration_kit_root_path"], capsules[0])

    if run["log_path"] is None:
        logs = sorted((run_root / "logs").glob("*.log"))
        if logs:
            run["log_path"] = rel_to(project["orchestration_kit_root_path"], logs[0])

    manifest_full = parse_manifest_full(project["orchestration_kit_root_path"], run["manifest_path"])
    metadata = manifest_full.get("metadata") if isinstance(manifest_full.get("metadata"), dict) else {}
    if metadata:
        if run["parent_run_id"] is None and isinstance(metadata.get("parent_run_id"), str):
            run["parent_run_id"] = metadata["parent_run_id"]
        if run["kit"] is None and isinstance(metadata.get("kit"), str):
            run["kit"] = metadata["kit"]
        if run["phase"] is None and isinstance(metadata.get("phase"), str):
            run["phase"] = metadata["phase"]
        if run["started_at"] is None and isinstance(metadata.get("started_at"), str):
            run["started_at"] = metadata["started_at"]
        if run["finished_at"] is None and isinstance(metadata.get("finished_at"), str):
            run["finished_at"] = metadata["finished_at"]
        if run["exit_code"] is None and isinstance(metadata.get("exit_code"), int):
            run["exit_code"] = metadata["exit_code"]
        if run["cwd"] is None and isinstance(metadata.get("cwd"), str):
            run["cwd"] = metadata["cwd"]
        if isinstance(metadata.get("project_root"), str):
            run["project_root"] = metadata["project_root"]
        if isinstance(metadata.get("orchestration_kit_root"), str):
            run["orchestration_kit_root"] = metadata["orchestration_kit_root"]
        if isinstance(metadata.get("agent_runtime"), str):
            run["agent_runtime"] = metadata["agent_runtime"]
        if isinstance(metadata.get("host"), str):
            run["host"] = metadata["host"]
        if isinstance(metadata.get("pid"), int):
            run["pid"] = metadata["pid"]
        if run["reasoning"] is None and isinstance(metadata.get("reasoning"), str):
            run["reasoning"] = metadata["reasoning"]

    # Extract experiment_name from command[]
    if metadata:
        exp_name = _extract_experiment_name(metadata)
        if exp_name:
            run["experiment_name"] = exp_name

    # Extract verdict from analysis.md in result artifacts
    artifact_index = manifest_full.get("artifact_index")
    if isinstance(artifact_index, dict):
        tracked = artifact_index.get("tracked")
        if isinstance(tracked, list):
            project_root_path = project.get("project_root_path") or Path(project["project_root"])
            verdict = _extract_verdict(project_root_path, tracked)
            if verdict:
                run["verdict"] = verdict

    if run["finished_at"] is None:
        run["status"] = "running"
    elif run["exit_code"] == 0:
        run["status"] = "ok"
    else:
        run["status"] = "failed"

    return run, sorted(requests.values(), key=lambda x: (x.get("enqueued_ts") or "", x["request_id"]))
