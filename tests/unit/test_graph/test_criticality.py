"""Unit tests for shipwreck.graph.criticality."""

from __future__ import annotations

import pytest

from shipwreck.graph.criticality import compute_criticality
from shipwreck.models import (
    Confidence,
    EdgeType,
    Graph,
    GraphEdge,
    GraphNode,
    SourceLocation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node(node_id: str) -> GraphNode:
    return GraphNode(id=node_id, canonical=node_id)


def _source_location() -> SourceLocation:
    return SourceLocation(repo="repo", file="Dockerfile", line=1, parser="dockerfile")


def _edge(source: str, target: str, relationship: EdgeType = EdgeType.BUILDS_FROM) -> GraphEdge:
    return GraphEdge(
        source=source,
        target=target,
        relationship=relationship,
        confidence=Confidence.HIGH,
        source_location=_source_location(),
    )


def _graph(*node_ids: str, edges: list[GraphEdge] | None = None) -> Graph:
    graph = Graph()
    for nid in node_ids:
        graph.nodes[nid] = _node(nid)
    graph.edges = edges or []
    return graph


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNoDependentsScoreZero:
    """Node with no dependents has criticality 0."""

    def test_no_dependents_score_zero(self) -> None:
        graph = _graph("A")
        compute_criticality(graph)
        assert graph.nodes["A"].criticality == 0.0

    def test_multiple_isolated_nodes(self) -> None:
        graph = _graph("A", "B", "C")
        compute_criticality(graph)
        for nid in ("A", "B", "C"):
            assert graph.nodes[nid].criticality == 0.0


class TestDirectDependents:
    """Node with 2 direct dependents has criticality 2."""

    def test_direct_dependents(self) -> None:
        # B and C both build from A
        graph = _graph(
            "A",
            "B",
            "C",
            edges=[
                _edge("B", "A", EdgeType.BUILDS_FROM),
                _edge("C", "A", EdgeType.BUILDS_FROM),
            ],
        )
        compute_criticality(graph)
        assert graph.nodes["A"].criticality == 2.0
        assert graph.nodes["B"].criticality == 0.0
        assert graph.nodes["C"].criticality == 0.0

    def test_consumes_counts_as_dependent(self) -> None:
        # B consumes A — A should get a direct dependent score
        graph = _graph("A", "B", edges=[_edge("B", "A", EdgeType.CONSUMES)])
        compute_criticality(graph)
        assert graph.nodes["A"].criticality == 1.0

    def test_produces_does_not_count(self) -> None:
        # A produces B — B should NOT gain a dependent from this
        graph = _graph("A", "B", edges=[_edge("A", "B", EdgeType.PRODUCES)])
        compute_criticality(graph)
        assert graph.nodes["B"].criticality == 0.0


class TestTransitiveDependents:
    """Transitive dependents score at 0.5 each."""

    def test_transitive_dependents(self) -> None:
        # A ← B ← C  (C builds from B, B builds from A)
        graph = _graph(
            "A",
            "B",
            "C",
            edges=[
                _edge("B", "A"),
                _edge("C", "B"),
            ],
        )
        compute_criticality(graph)
        # A: direct={B}, transitive={C}  → 1 + 0.5*1 = 1.5
        assert graph.nodes["A"].criticality == pytest.approx(1.5)
        # B: direct={C}, transitive={}   → 1
        assert graph.nodes["B"].criticality == pytest.approx(1.0)
        # C: no dependents               → 0
        assert graph.nodes["C"].criticality == 0.0

    def test_transitive_only_scored_once(self) -> None:
        """A node reachable via two paths should only be counted once."""
        # A is base; B and C both build from A; D builds from both B and C.
        # D is a transitive dependent of A via both B and C.
        graph = _graph(
            "A",
            "B",
            "C",
            "D",
            edges=[
                _edge("B", "A"),
                _edge("C", "A"),
                _edge("D", "B"),
                _edge("D", "C"),
            ],
        )
        compute_criticality(graph)
        # A: direct={B, C} → 2; transitive={D} → 0.5*1 = 0.5; total = 2.5
        assert graph.nodes["A"].criticality == pytest.approx(2.5)


class TestChainOfThree:
    """A → B → C: C's criticality = 1 (B direct) + 0.5 (A transitive)."""

    def test_chain_of_three(self) -> None:
        # A and B build from C.  A also builds from B.
        # Edge direction: source builds FROM target.
        # So: A BUILDS_FROM B, B BUILDS_FROM C
        graph = _graph(
            "C",
            "B",
            "A",
            edges=[
                _edge("A", "B"),  # A builds from B
                _edge("B", "C"),  # B builds from C
            ],
        )
        compute_criticality(graph)
        # C: direct={B}, transitive={A}  → 1 + 0.5*1 = 1.5
        assert graph.nodes["C"].criticality == pytest.approx(1.5)
        # B: direct={A}, transitive={}   → 1
        assert graph.nodes["B"].criticality == pytest.approx(1.0)
        # A: no dependents               → 0
        assert graph.nodes["A"].criticality == 0.0

    def test_longer_chain(self) -> None:
        # D ← C ← B ← A  (each builds from the next)
        graph = _graph(
            "A",
            "B",
            "C",
            "D",
            edges=[
                _edge("B", "A"),
                _edge("C", "B"),
                _edge("D", "C"),
            ],
        )
        compute_criticality(graph)
        # A: direct={B}, transitive={C, D} → 1 + 0.5*2 = 2.0
        assert graph.nodes["A"].criticality == pytest.approx(2.0)
        # B: direct={C}, transitive={D}    → 1 + 0.5*1 = 1.5
        assert graph.nodes["B"].criticality == pytest.approx(1.5)
        # C: direct={D}, transitive={}     → 1.0
        assert graph.nodes["C"].criticality == pytest.approx(1.0)
        # D: no dependents                 → 0
        assert graph.nodes["D"].criticality == 0.0
