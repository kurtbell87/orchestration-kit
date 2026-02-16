"""DAG layout algorithm and /api/dag payload builder.

Uses Kahn's topological sort for layer assignment and a barycenter
heuristic for crossing reduction.  Returns positioned nodes + edges
as JSON ready for SVG rendering.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from typing import Any

from .config import db_path
from .schema import ensure_schema


# Layout constants — top-to-bottom orientation
NODE_W = 180
NODE_H = 44
LAYER_GAP_Y = 80       # vertical gap between layers (rows)
NODE_GAP_X = 220        # horizontal gap between sibling nodes in same layer
PAD_X = 40
PAD_Y = 40


def _load_runs_and_requests(project_id: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)

    if project_id:
        runs = conn.execute(
            "SELECT run_id, parent_run_id, kit, phase, status, started_at, reasoning, experiment_name FROM runs WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        reqs = conn.execute(
            "SELECT parent_run_id, child_run_id, from_kit, from_phase, to_kit, to_phase, status, reasoning "
            "FROM requests WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    else:
        runs = conn.execute(
            "SELECT run_id, parent_run_id, kit, phase, status, started_at, reasoning, experiment_name FROM runs"
        ).fetchall()
        reqs = conn.execute(
            "SELECT parent_run_id, child_run_id, from_kit, from_phase, to_kit, to_phase, status, reasoning FROM requests"
        ).fetchall()

    conn.close()
    return [dict(r) for r in runs], [dict(r) for r in reqs]


def _build_adjacency(
    runs: list[dict[str, Any]],
    requests: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], set[tuple[str, str, str]]]:
    """Return (node_info, children_map, edge_set)."""
    nodes: dict[str, dict[str, Any]] = {}
    for r in runs:
        rid = str(r["run_id"])
        nodes[rid] = {
            "id": rid,
            "kit": r.get("kit") or "?",
            "phase": r.get("phase") or "?",
            "status": r.get("status") or "unknown",
            "started_at": r.get("started_at") or "",
            "reasoning": r.get("reasoning") or "",
            "experiment_name": r.get("experiment_name") or "",
        }

    children: dict[str, list[str]] = defaultdict(list)
    edges: set[tuple[str, str, str]] = set()  # (source, target, type)

    # Track which nodes already have explicit parent edges
    has_explicit_parent: set[str] = set()

    for r in runs:
        rid = str(r["run_id"])
        parent = r.get("parent_run_id")
        if isinstance(parent, str) and parent in nodes:
            children[parent].append(rid)
            edges.add((parent, rid, "parent"))
            has_explicit_parent.add(rid)

    for req in requests:
        p = req.get("parent_run_id")
        c = req.get("child_run_id")
        if isinstance(p, str) and isinstance(c, str) and p in nodes and c in nodes:
            children[p].append(c)
            edges.add((p, c, "interop"))
            has_explicit_parent.add(c)

    # Infer sequential edges from timestamps within the same kit.
    # Group runs by kit, sort by started_at, and connect consecutive phases
    # for runs that have no explicit parent linkage.
    by_kit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        kit = r.get("kit") or "?"
        started = r.get("started_at") or ""
        if started:
            by_kit[kit].append(r)

    for kit, kit_runs in by_kit.items():
        kit_runs.sort(key=lambda x: (x.get("started_at") or "", str(x.get("run_id") or "")))
        for i in range(1, len(kit_runs)):
            prev_id = str(kit_runs[i - 1]["run_id"])
            curr_id = str(kit_runs[i]["run_id"])
            # Only infer edge if current node has no explicit parent and
            # the edge doesn't already exist
            if curr_id not in has_explicit_parent and (prev_id, curr_id, "parent") not in edges:
                children[prev_id].append(curr_id)
                edges.add((prev_id, curr_id, "inferred"))

    # Experiment lineage: connect runs sharing the same experiment_name chronologically
    by_experiment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        exp = r.get("experiment_name") or ""
        started = r.get("started_at") or ""
        if exp and started:
            by_experiment[exp].append(r)

    for exp, exp_runs in by_experiment.items():
        exp_runs.sort(key=lambda x: (x.get("started_at") or "", str(x.get("run_id") or "")))
        for i in range(1, len(exp_runs)):
            prev_id = str(exp_runs[i - 1]["run_id"])
            curr_id = str(exp_runs[i]["run_id"])
            # Skip if any edge already exists between these nodes
            if any((prev_id, curr_id, t) in edges for t in ("parent", "interop", "inferred", "experiment")):
                continue
            children[prev_id].append(curr_id)
            edges.add((prev_id, curr_id, "experiment"))

    return nodes, children, edges


def _topo_layers(nodes: dict[str, dict[str, Any]], children: dict[str, list[str]]) -> dict[str, int]:
    """Assign each node to a layer using Kahn's algorithm (longest path)."""
    in_degree: dict[str, int] = {nid: 0 for nid in nodes}
    for parent, kids in children.items():
        for kid in kids:
            if kid in in_degree:
                in_degree[kid] += 1

    queue: deque[str] = deque()
    for nid, deg in in_degree.items():
        if deg == 0:
            queue.append(nid)

    layer: dict[str, int] = {}
    while queue:
        nid = queue.popleft()
        if nid not in layer:
            layer[nid] = 0
        for kid in children.get(nid, []):
            if kid not in nodes:
                continue
            layer[kid] = max(layer.get(kid, 0), layer[nid] + 1)
            in_degree[kid] -= 1
            if in_degree[kid] <= 0:
                queue.append(kid)

    # Nodes not reached (cycles) get layer 0
    for nid in nodes:
        if nid not in layer:
            layer[nid] = 0

    return layer


def _barycenter_order(
    layers_map: dict[str, int],
    children: dict[str, list[str]],
    nodes: dict[str, dict[str, Any]],
) -> dict[int, list[str]]:
    """Order nodes within each layer using barycenter heuristic."""
    by_layer: dict[int, list[str]] = defaultdict(list)
    for nid, lyr in layers_map.items():
        by_layer[lyr].append(nid)

    # Initial order: sort by started_at then id for stability
    for lyr in by_layer:
        by_layer[lyr].sort(key=lambda nid: (nodes[nid].get("started_at") or "", nid))

    # Build reverse map: child -> parents
    parents_of: dict[str, list[str]] = defaultdict(list)
    for parent, kids in children.items():
        for kid in kids:
            parents_of[kid].append(parent)

    max_layer = max(by_layer.keys()) if by_layer else 0

    # Sweep forward and back a few times
    for _ in range(4):
        # Forward sweep
        for lyr in range(1, max_layer + 1):
            if lyr not in by_layer:
                continue
            prev_order = {nid: idx for idx, nid in enumerate(by_layer.get(lyr - 1, []))}
            scored: list[tuple[float, str]] = []
            for nid in by_layer[lyr]:
                pars = [p for p in parents_of.get(nid, []) if p in prev_order]
                if pars:
                    bc = sum(prev_order[p] for p in pars) / len(pars)
                else:
                    bc = float("inf")
                scored.append((bc, nid))
            scored.sort()
            by_layer[lyr] = [nid for _, nid in scored]

        # Backward sweep
        for lyr in range(max_layer - 1, -1, -1):
            if lyr not in by_layer:
                continue
            next_order = {nid: idx for idx, nid in enumerate(by_layer.get(lyr + 1, []))}
            scored = []
            for nid in by_layer[lyr]:
                kids = [k for k in children.get(nid, []) if k in next_order]
                if kids:
                    bc = sum(next_order[k] for k in kids) / len(kids)
                else:
                    bc = float("inf")
                scored.append((bc, nid))
            scored.sort()
            by_layer[lyr] = [nid for _, nid in scored]

    return dict(by_layer)


def dag_payload(project_id: str | None) -> dict[str, Any]:
    """Build the full DAG layout payload."""
    runs, requests = _load_runs_and_requests(project_id)
    if not runs:
        return {"nodes": [], "edges": [], "width": 0, "height": 0}

    nodes, children, edge_set = _build_adjacency(runs, requests)
    layers_map = _topo_layers(nodes, children)
    ordered_layers = _barycenter_order(layers_map, children, nodes)

    # Assign coordinates — top-to-bottom layout
    # Layers are rows (y increases), siblings spread horizontally (x increases)
    positions: dict[str, tuple[float, float]] = {}
    max_x = 0.0
    max_y = 0.0

    for lyr_idx, node_ids in sorted(ordered_layers.items()):
        y = PAD_Y + lyr_idx * LAYER_GAP_Y
        for slot, nid in enumerate(node_ids):
            x = PAD_X + slot * NODE_GAP_X
            positions[nid] = (x, y)
            max_x = max(max_x, x + NODE_W)
            max_y = max(max_y, y + NODE_H)

    result_nodes: list[dict[str, Any]] = []
    for nid, info in nodes.items():
        x, y = positions.get(nid, (PAD_X, PAD_Y))
        result_nodes.append({
            "id": nid,
            "label": f"{info['kit']}.{info['phase']}",
            "status": info["status"],
            "kit": info["kit"],
            "phase": info["phase"],
            "reasoning": info.get("reasoning") or "",
            "started_at": info.get("started_at") or "",
            "experiment_name": info.get("experiment_name") or "",
            "x": x,
            "y": y,
            "width": NODE_W,
            "height": NODE_H,
        })

    # Build interop reasoning lookup
    interop_reasoning: dict[tuple[str, str], str] = {}
    for req in requests:
        p = req.get("parent_run_id")
        c = req.get("child_run_id")
        r = req.get("reasoning")
        if isinstance(p, str) and isinstance(c, str) and isinstance(r, str) and r:
            interop_reasoning[(p, c)] = r

    result_edges: list[dict[str, Any]] = []
    for src, tgt, etype in sorted(edge_set):
        edge: dict[str, Any] = {
            "source": src,
            "target": tgt,
            "type": etype,
        }
        if etype == "interop" and (src, tgt) in interop_reasoning:
            edge["reasoning"] = interop_reasoning[(src, tgt)]
        result_edges.append(edge)

    return {
        "nodes": result_nodes,
        "edges": result_edges,
        "width": max_x + PAD_X,
        "height": max_y + PAD_Y,
    }
