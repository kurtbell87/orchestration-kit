"""Canned Cypher queries for reasoning chain auditing and DAG analysis.

Requires the ``neo4j`` Python package.
"""
from __future__ import annotations

from typing import Any


def trace_reasoning_chain(driver: Any, run_id: str) -> list[dict[str, Any]]:
    """Trace the full ancestor reasoning chain for a run.

    Returns nodes from root to target with their reasoning, ordered by
    path depth (root first).
    """
    query = """
    MATCH path = (target:Run {run_id: $run_id})-[:CHILD_OF|INTEROP*0..]->(ancestor:Run)
    WITH ancestor, length(path) AS depth
    ORDER BY depth DESC
    RETURN DISTINCT ancestor.run_id AS run_id,
           ancestor.kit AS kit,
           ancestor.phase AS phase,
           ancestor.status AS status,
           ancestor.reasoning AS reasoning,
           depth
    ORDER BY depth DESC
    """
    with driver.session() as session:
        result = session.run(query, run_id=run_id)
        return [dict(record) for record in result]


def find_failed_runs_with_ancestors(driver: Any, project_id: str) -> list[dict[str, Any]]:
    """Find all failed runs in a project and their ancestor paths.

    Returns failed nodes with their ancestor chain for debugging.
    """
    query = """
    MATCH (failed:Run {project_id: $project_id, status: 'failed'})
    OPTIONAL MATCH path = (failed)-[:CHILD_OF|INTEROP*1..]->(ancestor:Run)
    WITH failed,
         COLLECT(DISTINCT {
           run_id: ancestor.run_id,
           kit: ancestor.kit,
           phase: ancestor.phase,
           status: ancestor.status,
           reasoning: ancestor.reasoning
         }) AS ancestors
    RETURN failed.run_id AS run_id,
           failed.kit AS kit,
           failed.phase AS phase,
           failed.reasoning AS reasoning,
           failed.exit_code AS exit_code,
           ancestors
    """
    with driver.session() as session:
        result = session.run(query, project_id=project_id)
        return [dict(record) for record in result]


def interop_edges_with_reasoning(driver: Any, project_id: str) -> list[dict[str, Any]]:
    """Return all interop (cross-kit) edges for a project with reasoning.

    Useful for auditing all cross-kit handoffs and their justifications.
    """
    query = """
    MATCH (a:Run {project_id: $project_id})-[r:INTEROP]->(b:Run)
    RETURN a.run_id AS from_run_id,
           a.kit AS from_kit,
           a.phase AS from_phase,
           r.request_id AS request_id,
           r.action AS action,
           r.reasoning AS reasoning,
           b.run_id AS to_run_id,
           b.kit AS to_kit,
           b.phase AS to_phase,
           b.status AS to_status
    ORDER BY a.started_at
    """
    with driver.session() as session:
        result = session.run(query, project_id=project_id)
        return [dict(record) for record in result]


def critical_path(driver: Any, project_id: str) -> list[dict[str, Any]]:
    """Find the longest path from any root to any leaf in the DAG.

    The critical path represents the bottleneck chain in the pipeline.
    """
    query = """
    MATCH (root:Run {project_id: $project_id})
    WHERE NOT (root)-[:CHILD_OF|INTEROP]->()
    MATCH (leaf:Run {project_id: $project_id})
    WHERE NOT ()-[:CHILD_OF|INTEROP]->(leaf)
    MATCH path = (leaf)-[:CHILD_OF|INTEROP*0..]->(root)
    WITH path, length(path) AS len
    ORDER BY len DESC
    LIMIT 1
    UNWIND nodes(path) AS node
    RETURN node.run_id AS run_id,
           node.kit AS kit,
           node.phase AS phase,
           node.status AS status,
           node.reasoning AS reasoning,
           node.started_at AS started_at,
           node.finished_at AS finished_at
    """
    with driver.session() as session:
        result = session.run(query, project_id=project_id)
        return [dict(record) for record in result]
