"""CLI entry point: argparse commands and main()."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import (
    REPO_ROOT,
    now_iso,
    db_path,
    service_state_path,
    service_log_path,
    coerce_path,
    current_orchestration_kit_root,
    current_project_root,
)
from .registry import load_registry, upsert_registry_project, remove_registry_project
from .schema import ensure_schema
from .indexing import index_projects, prepare_projects, maybe_seed_registry
from .service import load_service_state, save_service_state, pid_alive, healthcheck
from .server import DashboardHandler, DashboardServer

def cmd_register(args: argparse.Namespace) -> int:
    default_mk = current_orchestration_kit_root()
    orchestration_kit_root = coerce_path(args.orchestration_kit_root, default_mk)
    project_root = coerce_path(args.project_root, current_project_root(orchestration_kit_root))

    if not (orchestration_kit_root / "tools" / "kit").is_file():
        print(f"Error: not a orchestration-kit root: {orchestration_kit_root}")
        return 2

    record = upsert_registry_project(
        orchestration_kit_root=orchestration_kit_root,
        project_root=project_root,
        label=args.label,
    )
    print(json.dumps(record, sort_keys=True))
    return 0


def cmd_unregister(args: argparse.Namespace) -> int:
    removed = remove_registry_project(args.project_id)
    if not removed:
        print(f"No project found for id={args.project_id}")
        return 1
    print(f"removed project_id={args.project_id}")
    return 0


def cmd_projects(_: argparse.Namespace) -> int:
    projects = load_registry()
    if not projects:
        print("[]")
        return 0
    print(json.dumps(sorted(projects, key=lambda x: x["label"].lower()), indent=2, sort_keys=True))
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    projects = maybe_seed_registry()
    prepared = prepare_projects(projects)
    if args.project_id:
        prepared = [p for p in prepared if p["project_id"] == args.project_id]

    if not prepared:
        print("No registered projects to index.")
        return 1

    result = index_projects(
        prepared,
        cleanup_stale_projects=not bool(args.project_id),
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_ensure_service(args: argparse.Namespace) -> int:
    host = args.host
    port = int(args.port)
    wait_seconds = max(int(args.wait_seconds), 1)
    url = f"http://{host}:{port}"
    state = load_service_state()

    if healthcheck(host=host, port=port):
        payload = {
            "status": "running",
            "started": False,
            "host": host,
            "port": port,
            "url": url,
            "pid": state.get("pid"),
            "service_state_path": str(service_state_path()),
            "service_log_path": str(service_log_path()),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0

    pid_raw = state.get("pid")
    if isinstance(pid_raw, int) and pid_alive(pid_raw):
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if healthcheck(host=host, port=port):
                payload = {
                    "status": "running",
                    "started": False,
                    "host": host,
                    "port": port,
                    "url": url,
                    "pid": pid_raw,
                    "service_state_path": str(service_state_path()),
                    "service_log_path": str(service_log_path()),
                }
                print(json.dumps(payload, sort_keys=True))
                return 0
            time.sleep(0.2)

    log_path = service_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        os.sys.executable,
        str(Path(__file__).resolve().parent.parent / "tools" / "dashboard"),
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]
    env = os.environ.copy()
    with log_path.open("a", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    started = False
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if healthcheck(host=host, port=port):
            started = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    payload = {
        "status": "running" if started else "failed",
        "started": True,
        "host": host,
        "port": port,
        "url": url,
        "pid": proc.pid,
        "service_state_path": str(service_state_path()),
        "service_log_path": str(str(log_path)),
    }

    if started:
        save_service_state(
            {
                "pid": proc.pid,
                "host": host,
                "port": port,
                "url": url,
                "started_at": now_iso(),
                "updated_at": now_iso(),
                "repo_root": str(REPO_ROOT),
            }
        )
        print(json.dumps(payload, sort_keys=True))
        return 0

    print(json.dumps(payload, sort_keys=True))
    return 1


def cmd_service_status(args: argparse.Namespace) -> int:
    state = load_service_state()
    host = args.host or (state.get("host") if isinstance(state.get("host"), str) else os.getenv("ORCHESTRATION_KIT_DASHBOARD_HOST", "127.0.0.1"))
    port_raw = args.port if args.port else state.get("port")
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = int(os.getenv("ORCHESTRATION_KIT_DASHBOARD_PORT", "7340"))

    running = healthcheck(host=host, port=port)
    pid = state.get("pid") if isinstance(state.get("pid"), int) else None

    payload = {
        "status": "running" if running else "stopped",
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}",
        "pid": pid,
        "pid_alive": bool(pid_alive(pid)) if isinstance(pid, int) else False,
        "service_state_path": str(service_state_path()),
        "service_log_path": str(service_log_path()),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if running else 1


def cmd_stop_service(args: argparse.Namespace) -> int:
    state = load_service_state()
    pid = state.get("pid") if isinstance(state.get("pid"), int) else None
    host = state.get("host") if isinstance(state.get("host"), str) else os.getenv("ORCHESTRATION_KIT_DASHBOARD_HOST", "127.0.0.1")
    port_raw = state.get("port")
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = int(os.getenv("ORCHESTRATION_KIT_DASHBOARD_PORT", "7340"))

    stopped_pid = False
    if isinstance(pid, int) and pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            stopped_pid = True
        except OSError:
            stopped_pid = False

    deadline = time.time() + max(int(args.wait_seconds), 1)
    while time.time() < deadline:
        if not healthcheck(host=host, port=port):
            break
        time.sleep(0.2)

    running = healthcheck(host=host, port=port)
    if not running and service_state_path().exists():
        service_state_path().unlink(missing_ok=True)

    payload = {
        "status": "running" if running else "stopped",
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}",
        "pid": pid,
        "stop_signal_sent": stopped_pid,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not running else 1


def cmd_serve(args: argparse.Namespace) -> int:
    projects = maybe_seed_registry()
    prepared = prepare_projects(projects)
    if args.project_id:
        prepared = [p for p in prepared if p["project_id"] == args.project_id]

    if args.project_id and not prepared:
        print("No registered projects to index/serve.")
        return 1

    if prepared:
        seed = index_projects(
            prepared,
            cleanup_stale_projects=not bool(args.project_id),
        )
    else:
        conn = sqlite3.connect(str(db_path()))
        ensure_schema(conn)
        conn.close()
        seed = {
            "projects_indexed": 0,
            "runs_indexed": 0,
            "requests_indexed": 0,
            "missing_roots": [],
            "db_path": str(db_path()),
        }
    print(f"dashboard indexed: {json.dumps(seed, sort_keys=True)}", flush=True)

    host = args.host
    port = args.port
    server = DashboardServer((host, port), DashboardHandler)

    print(f"dashboard ready url=http://{host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tools/dashboard", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_register = sub.add_parser("register", help="Register or update a project for global indexing")
    p_register.add_argument("--orchestration-kit-root", default=None)
    p_register.add_argument("--project-root", default=None)
    p_register.add_argument("--label", default=None)
    p_register.set_defaults(func=cmd_register)

    p_unregister = sub.add_parser("unregister", help="Unregister a project id")
    p_unregister.add_argument("--project-id", required=True)
    p_unregister.set_defaults(func=cmd_unregister)

    p_projects = sub.add_parser("projects", help="List registered projects")
    p_projects.set_defaults(func=cmd_projects)

    p_index = sub.add_parser("index", help="Rebuild dashboard index from registered projects")
    p_index.add_argument("--project-id", default=None)
    p_index.set_defaults(func=cmd_index)

    p_ensure = sub.add_parser("ensure-service", help="Ensure the dashboard HTTP service is running")
    p_ensure.add_argument("--host", default=os.getenv("ORCHESTRATION_KIT_DASHBOARD_HOST", "127.0.0.1"))
    p_ensure.add_argument("--port", type=int, default=int(os.getenv("ORCHESTRATION_KIT_DASHBOARD_PORT", "7340")))
    p_ensure.add_argument("--wait-seconds", type=int, default=5)
    p_ensure.set_defaults(func=cmd_ensure_service)

    p_status = sub.add_parser("service-status", help="Show dashboard service health/status")
    p_status.add_argument("--host", default=None)
    p_status.add_argument("--port", type=int, default=0)
    p_status.set_defaults(func=cmd_service_status)

    p_stop = sub.add_parser("stop-service", help="Stop dashboard service using stored PID")
    p_stop.add_argument("--wait-seconds", type=int, default=5)
    p_stop.set_defaults(func=cmd_stop_service)

    p_serve = sub.add_parser("serve", help="Run HTTP dashboard server")
    p_serve.add_argument("--host", default=os.getenv("ORCHESTRATION_KIT_DASHBOARD_HOST", "127.0.0.1"))
    p_serve.add_argument("--port", type=int, default=int(os.getenv("ORCHESTRATION_KIT_DASHBOARD_PORT", "7340")))
    p_serve.add_argument("--project-id", default=None)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str]) -> int:
    if not argv:
        host = os.getenv("ORCHESTRATION_KIT_DASHBOARD_HOST", "127.0.0.1")
        port = int(os.getenv("ORCHESTRATION_KIT_DASHBOARD_PORT", "7340"))
        ensure_args = argparse.Namespace(host=host, port=port, wait_seconds=5)
        rc = cmd_ensure_service(ensure_args)
        status_args = argparse.Namespace(host=host, port=port)
        cmd_service_status(status_args)
        return rc

    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
