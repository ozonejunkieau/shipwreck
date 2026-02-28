"""Compute criticality scores for all nodes in a graph."""

from __future__ import annotations

from collections import defaultdict, deque

from shipwreck.models import EdgeType, Graph


def compute_criticality(graph: Graph) -> None:
    """Compute criticality scores for all nodes in-place.

    Criticality measures how many things would be affected if a given image had
    a problem:

        criticality = direct_dependents + 0.5 * transitive_dependents

    A "dependent" of node X is any node Y where Y has an edge targeting X with
    relationship BUILDS_FROM or CONSUMES (i.e. Y relies on X at build or
    runtime).  PRODUCES edges are not counted — the producing file is not a
    "dependent" of the produced image.

    Args:
        graph: The graph to score (modified in-place).
    """
    # Build a map from each node to the set of nodes that *directly* depend on it.
    direct_dependents: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.relationship in (EdgeType.BUILDS_FROM, EdgeType.CONSUMES):
            # edge.source depends on edge.target → edge.target gains a dependent
            direct_dependents[edge.target].add(edge.source)

    for node_id, node in graph.nodes.items():
        direct: set[str] = direct_dependents.get(node_id, set())

        # BFS over the "dependents" relation to discover transitive dependents.
        visited: set[str] = set(direct)
        queue: deque[str] = deque(direct)
        while queue:
            current = queue.popleft()
            for dep in direct_dependents.get(current, set()):
                if dep not in visited:
                    visited.add(dep)
                    queue.append(dep)

        transitive = visited - direct
        node.criticality = len(direct) + 0.5 * len(transitive)
