"""Shared pytest fixtures for Shipwreck tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from shipwreck.models import (
    Confidence,
    EdgeType,
    Graph,
    GraphEdge,
    GraphNode,
    ImageSource,
    SourceLocation,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
DOCKERFILES_DIR = FIXTURES_DIR / "dockerfiles"
BAKE_DIR = FIXTURES_DIR / "bake"
COMPOSE_DIR = FIXTURES_DIR / "compose"
FALLBACK_DIR = FIXTURES_DIR / "fallback"


def make_source(
    repo: str = "test-repo",
    file: str = "Dockerfile",
    line: int = 1,
    parser: str = "dockerfile",
) -> SourceLocation:
    """Build a SourceLocation for tests."""
    return SourceLocation(repo=repo, file=file, line=line, parser=parser)


def make_graph(num_nodes: int = 2) -> Graph:
    """Build a minimal test Graph with the given number of nodes.

    Creates a simple chain: node_0 builds_from node_1 builds_from ... node_n-1.
    """
    graph = Graph(generated_at="2025-01-01T00:00:00Z")

    for i in range(num_nodes):
        node_id = f"registry.example.com/image-{i}"
        graph.nodes[node_id] = GraphNode(
            id=node_id,
            canonical=node_id,
            tags_referenced=[f"1.{i}"],
            classification="product" if i == 0 else "base",
            criticality=0.0,
            sources=[
                ImageSource(
                    repo="test-repo",
                    file="Dockerfile",
                    line=i + 1,
                    relationship=EdgeType.BUILDS_FROM if i > 0 else EdgeType.PRODUCES,
                    tag=f"1.{i}",
                )
            ],
        )

    # Chain edges
    node_ids = list(graph.nodes.keys())
    src = SourceLocation(repo="test-repo", file="Dockerfile", line=1, parser="dockerfile")
    for i in range(len(node_ids) - 1):
        graph.edges.append(
            GraphEdge(
                source=node_ids[i],
                target=node_ids[i + 1],
                relationship=EdgeType.BUILDS_FROM,
                confidence=Confidence.HIGH,
                source_location=src,
            )
        )

    graph.summary.total_images = num_nodes
    return graph


@pytest.fixture()
def simple_graph() -> Graph:
    """A simple two-node graph (product → base)."""
    return make_graph(2)


@pytest.fixture()
def chain_graph() -> Graph:
    """A three-node chain graph."""
    return make_graph(3)


@pytest.fixture()
def fixtures_dir() -> Path:
    """Return the path to the fixtures directory."""
    return FIXTURES_DIR
