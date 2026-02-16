#!/usr/bin/env python3
"""
resolve-deps.py — Topological sort of constructions from CONSTRUCTIONS.md

Usage:
    python3 scripts/resolve-deps.py CONSTRUCTIONS.md [--next] [--mark-blocked PRIORITY]

Output: JSON lines, one per actionable construction (not yet audited/proved),
in dependency order. Each line:
    {"priority":"P1","name":"...","spec":"...","status":"...","depends_on":[...],"blocked":false}

Options:
    --next              Print only the first non-blocked construction
    --mark-blocked P    Mark construction P and all downstream as Blocked
"""

import re
import sys
import json
from collections import defaultdict, deque


def parse_constructions(filepath):
    """Parse CONSTRUCTIONS.md and return dict of priority -> info."""
    with open(filepath) as f:
        content = f.read()

    constructions = {}
    for line in content.split('\n'):
        cells = [c.strip() for c in line.split('|')]
        cells = [c for c in cells if c]
        if len(cells) < 5:
            continue
        priority = cells[0]
        if not re.match(r'^P\d+$', priority):
            continue
        # Column order: Priority | Construction | Spec File | Status | Depends On | Notes
        depends_raw = cells[4] if len(cells) > 4 else '—'
        depends = [d.strip() for d in depends_raw.split(',') if d.strip() and d.strip() != '—']
        constructions[priority] = {
            'name': cells[1].strip('_ '),
            'spec': cells[2].strip('` '),
            'status': cells[3].strip().lower(),
            'depends_on': depends,
        }
    return constructions


def topo_sort(constructions):
    """Topological sort. Returns ordered list of priorities or raises on cycle."""
    graph = defaultdict(list)
    in_degree = defaultdict(int)

    for p in constructions:
        if p not in in_degree:
            in_degree[p] = 0
        for dep in constructions[p]['depends_on']:
            graph[dep].append(p)
            in_degree[p] += 1

    queue = deque(sorted([p for p in constructions if in_degree[p] == 0]))
    order = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in sorted(graph[node]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(constructions):
        cycle_nodes = [p for p in constructions if p not in order]
        print(json.dumps({'error': 'cycle', 'nodes': cycle_nodes}), file=sys.stderr)
        sys.exit(1)

    return order


def get_downstream(constructions, priority):
    """Get all constructions transitively depending on the given priority."""
    graph = defaultdict(list)
    for p, c in constructions.items():
        for dep in c['depends_on']:
            graph[dep].append(p)

    visited = set()
    queue = deque([priority])
    while queue:
        node = queue.popleft()
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return visited


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python3 resolve-deps.py CONSTRUCTIONS.md [--next] [--mark-blocked P]", file=sys.stderr)
        sys.exit(1)

    filepath = args[0]
    next_only = '--next' in args
    mark_blocked = None
    if '--mark-blocked' in args:
        idx = args.index('--mark-blocked')
        if idx + 1 < len(args):
            mark_blocked = args[idx + 1]

    constructions = parse_constructions(filepath)
    if not constructions:
        sys.exit(1)

    # Handle --mark-blocked: output downstream priorities as JSON
    if mark_blocked:
        downstream = get_downstream(constructions, mark_blocked)
        print(json.dumps({'blocked': sorted(downstream)}))
        sys.exit(0)

    order = topo_sort(constructions)

    actionable_statuses = ('not started', 'specified', 'constructed', 'formalized', 'revision')
    satisfied_statuses = ('audited', 'proved')

    for p in order:
        c = constructions[p]
        if c['status'] not in actionable_statuses:
            continue

        deps_met = all(
            constructions.get(d, {}).get('status', '') in satisfied_statuses
            for d in c['depends_on']
        )
        blocked = not deps_met and bool(c['depends_on'])

        entry = {
            'priority': p,
            'name': c['name'],
            'spec': c['spec'],
            'status': c['status'],
            'depends_on': c['depends_on'],
            'blocked': blocked,
        }

        if next_only:
            if not blocked:
                print(json.dumps(entry))
                sys.exit(0)
        else:
            print(json.dumps(entry))

    if next_only:
        # No non-blocked construction found
        sys.exit(1)


if __name__ == '__main__':
    main()
